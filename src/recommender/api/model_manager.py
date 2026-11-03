"""Keeps the current model in memory and swaps in new versions without downtime.

A background thread polls ``models/LATEST``. When it changes, the new version is
downloaded and checksum-verified *off the request path*, then swapped in with one
reference assignment, which is atomic in CPython. Requests in flight keep using the
object they already hold. If a new version is broken, the old model keeps serving and
the failure is counted in a metric, so a bad training run never takes the API down.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from recommender.core.model import Manifest, ModelError, RuleIndex, latest_version, load
from recommender.core.storage import ObjectNotFound, ObjectStore, StoreError

from . import metrics

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LoadedModel:
    manifest: Manifest
    index: RuleIndex
    loaded_at: float


class ModelManager:
    def __init__(self, store: ObjectStore, *, poll_seconds: float = 30.0) -> None:
        self.store = store
        self.poll_seconds = poll_seconds
        self._current: LoadedModel | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def current(self) -> LoadedModel | None:
        return self._current

    def refresh(self) -> bool:
        """Load LATEST if it differs from what we serve. Returns True when a swap happened."""
        try:
            version = latest_version(self.store)
        except ObjectNotFound:
            log.warning("no model published yet (models/LATEST missing)")
            return False
        except StoreError as exc:
            log.error("cannot read models/LATEST: %s", exc)
            metrics.MODEL_RELOADS.labels(result="error").inc()
            return False

        if self._current and self._current.manifest.version == version:
            return False
        try:
            manifest, index = load(self.store, version)
        except (ModelError, StoreError, ValueError, KeyError) as exc:
            log.error("model %s rejected, keeping the current one: %s", version, exc)
            metrics.MODEL_RELOADS.labels(result="error").inc()
            return False

        self._current = LoadedModel(manifest, index, time.time())
        metrics.MODEL_RELOADS.labels(result="success").inc()
        metrics.MODEL_RULES.set(len(index))
        metrics.MODEL_INFO.info({"version": manifest.version, "trained_at": manifest.trained_at})
        log.info("serving model %s (%d rules)", manifest.version, len(index))
        return True

    def start(self) -> None:
        self.refresh()
        self._thread = threading.Thread(target=self._loop, name="model-poller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.wait(self.poll_seconds):
            self.refresh()
