# ADR 4: One Helm chart, values per environment, Argo CD for delivery

**Status:** accepted

## Context
Raw manifests had the namespace and names hard-coded, so every environment needed edited copies.

## Decision
- A single chart in `deploy/helm/playlist-recommender`.
- Environment differences live only in `deploy/environments/values-<env>.yaml`.
- Argo CD Applications: **dev** auto-syncs `main` (prune and self-heal); **prod** has no automated sync, so a person reviews the diff and syncs.
- Prod pins an image tag. Promotion is a pull request that changes that tag.

## Consequences
- Every deploy is a reviewed git change. Drift is corrected by self-heal in dev and shown in the UI for prod.
- CI renders each environment and validates it against the Kubernetes and CRD schemas, so a broken values file fails before it reaches a cluster.
