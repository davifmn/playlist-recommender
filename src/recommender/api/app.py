"""HTTP API.

POST /v1/recommendations   tracks in, ranked recommendations out
GET  /v1/model             metadata of the model being served
POST /api/recommend        compatibility route with the original course spec
GET  /healthz              liveness: the process is up
GET  /readyz               readiness: a model is loaded (Kubernetes only routes traffic then)
GET  /metrics              Prometheus metrics
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from recommender import __version__
from recommender.core.storage import ObjectStore, store_from_env

from . import metrics
from .model_manager import LoadedModel, ModelManager
from .schemas import (
    LegacyRequest,
    LegacyResponse,
    ModelInfo,
    RecommendationRequest,
    RecommendationResponse,
    RecommendedTrack,
)

log = logging.getLogger("recommender.api")


class JsonFormatter(logging.Formatter):
    """One JSON object per line, so log aggregators (Loki, CloudWatch) can index fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {"level": record.levelname, "logger": record.name, "message": record.getMessage()}
        payload.update(getattr(record, "fields", {}))
        return json.dumps(payload)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), handlers=[handler], force=True)


def create_app(
    store: ObjectStore | None = None,
    *,
    poll_seconds: float | None = None,
    start_poller: bool = True,
) -> FastAPI:
    store = store if store is not None else store_from_env()
    poll = (
        poll_seconds
        if poll_seconds is not None
        else float(os.environ.get("RECSYS_POLL_SECONDS", 30))
    )
    manager = ModelManager(store, poll_seconds=poll)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_poller:
            manager.start()
        else:
            manager.refresh()
        yield
        manager.stop()

    app = FastAPI(title="Playlist Recommender", version=__version__, lifespan=lifespan)
    app.state.models = manager

    @app.middleware("http")
    async def observe(request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        path = route.path if route else "unmatched"  # template, so labels stay low-cardinality
        elapsed = time.perf_counter() - started
        metrics.LATENCY.labels(route=path).observe(elapsed)
        metrics.REQUESTS.labels(
            route=path, method=request.method, status=response.status_code
        ).inc()
        return response

    def serving() -> LoadedModel:
        model = manager.current
        if model is None:
            raise HTTPException(status_code=503, detail="no model loaded yet")
        return model

    @app.post("/v1/recommendations", response_model=RecommendationResponse)
    def recommend(body: RecommendationRequest) -> RecommendationResponse:
        model = serving()
        recs = model.index.recommend(body.tracks, limit=body.limit)
        if not recs:
            metrics.RECOMMENDATIONS_EMPTY.inc()
        return RecommendationResponse(
            tracks=[
                RecommendedTrack(track=r.track, confidence=r.confidence, lift=r.lift) for r in recs
            ],
            model=ModelInfo(
                version=model.manifest.version,
                trained_at=model.manifest.trained_at,
                rules=len(model.index),
            ),
        )

    @app.get("/v1/model")
    def model_info() -> dict:
        model = serving()
        m = model.manifest
        return {
            "version": m.version,
            "trained_at": m.trained_at,
            "params": m.params,
            "stats": m.stats,
            "dataset_sha256": m.dataset_sha256,
            "loaded_at": model.loaded_at,
        }

    @app.post("/api/recommend", response_model=LegacyResponse)
    def legacy_recommend(body: LegacyRequest) -> LegacyResponse:
        model = serving()
        recs = model.index.recommend(body.songs, limit=20)
        return LegacyResponse(
            songs=[r.track for r in recs],
            version=__version__,
            model_date=model.manifest.trained_at,
        )

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz() -> JSONResponse:
        model = manager.current
        if model is None:
            return JSONResponse({"status": "waiting for model"}, status_code=503)
        return JSONResponse({"status": "ready", "model": model.manifest.version})

    @app.get("/metrics")
    def prometheus() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


def main() -> None:  # pragma: no cover - exercised in the container
    import uvicorn

    configure_logging()
    uvicorn.run(
        create_app(),
        host="0.0.0.0",  # noqa: S104 - inside a container, behind a Service
        port=int(os.environ.get("PORT", 8080)),
        log_config=None,
        access_log=False,
    )
