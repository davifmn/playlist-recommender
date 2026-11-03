# playlist-recommender

[![CI](https://github.com/davifmn/playlist-recommender/actions/workflows/ci.yml/badge.svg)](https://github.com/davifmn/playlist-recommender/actions/workflows/ci.yml)

Playlist recommendations ("people who have these songs in a playlist also have…") served
by a stateless API on Kubernetes. A batch trainer mines association rules with FP-Growth;
models are versioned artifacts in object storage; the API hot-swaps new versions with no
downtime; deployments are GitOps with Helm and Argo CD.

> This started as my Cloud Computing course project at UFMG (a recommender deployed on a
> shared Kubernetes cluster with Argo CD). This repository is a rewrite meant to look like
> how a company would run it in production. See [what changed](#from-course-project-to-production-shape).

## Architecture

```
                         ┌───────────────────────── Kubernetes namespace ─────────────────────────┐
                         │                                                                        │
  Argo CD  ── syncs ──▶  │  CronJob (nightly) / Job (on deploy)        Deployment (HPA 2..8 pods)  │
  (Helm chart +          │  ┌──────────────────────────┐              ┌────────────────────────┐  │
   values per env)       │  │ trainer                  │              │ api (FastAPI)          │  │ ◀── clients
                         │  │  read datasets/*.csv     │              │  /v1/recommendations   │  │     POST tracks
                         │  │  FP-Growth → rules       │              │  /readyz /metrics      │  │
                         │  │  publish models/<ver>/   │              │  polls models/LATEST,  │  │
                         │  │  then flip models/LATEST │              │  verifies, swaps       │  │
                         │  └────────────┬─────────────┘              └───────────┬────────────┘  │
                         └───────────────┼────────────────────────────────────────┼──────────────┘
                                         ▼                                        ▼
                               ┌──────────────────────────────────────────────────────┐
                               │ S3 bucket (S3 / any S3-compatible store)              │
                               │  datasets/*.csv                                       │
                               │  models/<version>/{rules.jsonl.gz, manifest.json}     │
                               │  models/LATEST                                         │
                               └──────────────────────────────────────────────────────┘
                                         ▲
                               Prometheus scrapes /metrics ─▶ alerts (PrometheusRule)
```

| Path | What it is |
|---|---|
| `src/recommender/core/` | Shared library: storage abstraction (local / S3), model format, rule index |
| `src/recommender/trainer/` | Batch job: dataset loading and validation, FP-Growth, publishing |
| `src/recommender/api/` | FastAPI service: model manager (hot reload), metrics, JSON logs |
| `services/{api,trainer}/Dockerfile` | Two slim images from the same codebase. The API image has no pandas/scikit-learn. |
| `deploy/helm/playlist-recommender/` | Helm chart: Deployment, HPA, PDB, Service, Ingress, CronJob, on-deploy Job, NetworkPolicy, ServiceMonitor, PrometheusRule |
| `deploy/environments/` | `values-dev.yaml`, `values-prod.yaml`, `values-ci.yaml` |
| `deploy/argocd/` | AppProject plus dev (auto-sync) and prod (manual sync) Applications |
| `docs/adr/` | Architecture decision records |
| `docs/runbook.md` | What each alert means and what to do |

## API

```http
POST /v1/recommendations
{"tracks": ["spotify:track:…", "spotify:track:…"], "limit": 10}

200 OK
{"tracks": [{"track": "spotify:track:…", "confidence": 0.94, "lift": 6.1}, …],
 "model": {"version": "20261103T183012Z-3fa1c2d4", "trained_at": "…", "rules": 72}}
```

| Route | Purpose |
|---|---|
| `POST /v1/recommendations` | Ranked recommendations (input tracks are never recommended back) |
| `GET /v1/model` | Version, training parameters, dataset fingerprint and stats of the model being served |
| `POST /api/recommend` | Compatibility route with the original course contract (`{"songs": [...]}` → `{"songs", "version", "model_date"}`) |
| `GET /healthz`, `GET /readyz` | Liveness, and readiness (true only once a model is loaded) |
| `GET /metrics` | Prometheus: request rate/latency per route, empty-result counter, reloads, rule count, model version |

## From course project to production shape

| Course version | This version | Why |
|---|---|---|
| Model saved as a pickle on a shared PVC | Versioned JSON artifacts in object storage, with a manifest and SHA-256 | Unpickling is code execution; a PVC ties pods to one node/zone; versions allow rollback and audit ([ADR 1](docs/adr/0001-object-storage-for-models.md), [ADR 2](docs/adr/0002-json-not-pickle.md)) |
| API checked the file's mtime on every request | Background poller, verified load, atomic swap; a broken model is rejected and the old one keeps serving | No disk I/O on the request path, and a bad training run can't take the API down |
| Each request scanned every rule with `pandas.apply` | Inverted index from track to rules | Cost grows with matching rules, not model size; the API doesn't need pandas at all |
| Training as an Argo CD sync hook only | Nightly CronJob (`concurrencyPolicy: Forbid`) plus a training Job on deploy | Fresh models without a deploy, and a new environment still gets a model ([ADR 3](docs/adr/0003-training-schedule.md)) |
| Raw manifests, a hard-coded namespace, a placeholder image | Helm chart with dev/prod/ci values; Argo CD auto-syncs dev and needs a manual sync for prod | One chart, many environments; production changes are reviewed ([ADR 4](docs/adr/0004-helm-and-argocd-per-environment.md)) |
| Flask dev server, no probes | uvicorn, startup/readiness/liveness probes, HPA, PDB, zero-surge rolling update, topology spread | Survives node drains and scales with load |
| Root containers | Non-root, read-only root filesystem, all capabilities dropped, seccomp, NetworkPolicy | Least privilege |
| 80 MB dataset and 43 MB model committed to git | Data lives in the bucket; a synthetic generator in the real dataset's format is used for tests/dev | The real dataset can't be redistributed, and git is not a data store |
| No tests | 44 unit/API tests, chart rendered and schema-validated, plus an end-to-end test on a real Kubernetes cluster in CI | See below |

## Running it

**Tests only** (no Docker):

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
```

**Whole stack on a laptop** (Docker): S3 emulator, demo data, a training run and the API:

```sh
docker compose up --build
python scripts/smoke_test.py http://localhost:8080
```

**On Kubernetes** (kind), the same flow as CI:

```sh
make kind-up kind-deploy smoke
```

**With Argo CD** on an existing cluster:

```sh
kubectl apply -n argocd -f deploy/argocd/project.yaml -f deploy/argocd/app-dev.yaml
```

### Configuration

| Variable | Used by | Meaning |
|---|---|---|
| `RECSYS_STORE` | both | `s3` or `local` |
| `RECSYS_BUCKET`, `RECSYS_S3_ENDPOINT_URL` | both | Bucket, and an endpoint for S3-compatible stores |
| `RECSYS_LOCAL_ROOT` | both | Directory when `RECSYS_STORE=local` |
| `RECSYS_MIN_SUPPORT`, `RECSYS_MIN_CONFIDENCE`, `RECSYS_MAX_LEN` | trainer | FP-Growth thresholds (defaults 0.1 / 0.5 / 3) |
| `RECSYS_POLL_SECONDS` | api | How often to check `models/LATEST` (default 30) |

AWS credentials use the standard chain. In EKS, annotate the ServiceAccount for IRSA
(`values-prod.yaml`) instead of storing keys.

## CI

`.github/workflows/ci.yml`:

1. **python**: ruff and pytest on Python 3.12 and 3.13. S3 is mocked with moto.
2. **manifests**: `helm lint --strict` and `helm template` for every environment, validated with
   kubeconform against the Kubernetes 1.31 schemas and the Argo CD / Prometheus Operator CRDs.
3. **images**: builds both Dockerfiles.
4. **e2e**:
   - creates a 3-node kind cluster, loads the images and installs the chart (S3 emulator, demo data, training on deploy);
   - waits for readiness and checks that the planted patterns come back through the Service;
   - triggers a retraining from the CronJob and asserts the API switched to the new version without a restart.

`release.yml` publishes both images to GHCR when a `v*` tag is pushed.

## License

MIT. The demo data is synthetic. The original course used a subset of the Spotify Million
Playlist Dataset, which is not included here.
