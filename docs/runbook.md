# Runbook

## No model loaded
**Alert:** `RecommenderNoModelLoaded`. No API pod is Ready, so the Service has no endpoints.

1. Was a model ever published? Check that `models/LATEST` exists:
   `aws s3 cp s3://<bucket>/models/LATEST -`
2. If it doesn't, look at the training Jobs:
   `kubectl get jobs -l app.kubernetes.io/component=trainer`, then `kubectl logs job/<name>`.
3. If it exists, look at the API logs for `model ... rejected`, then follow "Model reload failing" below.
4. Pods without credentials (IRSA annotation missing) log `cannot read models/LATEST`.

## Model reload failing
**Alert:** `RecommenderModelReloadFailing`. A new version was published but rejected. The API is
**still serving the previous model**, so this is not an outage.

- `checksum mismatch`: the rules file doesn't match its manifest (partial upload, or someone edited it). Re-run training.
- `unsupported model schema`: the trainer is newer than the API. Deploy the API first.
- To roll back right away, write a previous version into `models/LATEST`. Pods pick it up within `RECSYS_POLL_SECONDS`.

## Training failed
**Alert:** `RecommenderTrainingFailed`.

- Exit code 1 with `no rules found`: the data changed or the thresholds are too strict. Nothing was
  published and the old model keeps serving. Adjust `trainer.minSupport` / `minConfidence` in the environment values.
- Exit code 2: configuration problem (bucket, credentials, missing columns in a dataset file).
- `OOMKilled`: the catalogue grew. Raise `trainer.resources.limits.memory`, or raise `minSupport` so more tracks are pruned before encoding.

## High latency
**Alert:** `RecommenderHighLatency` (p99 > 250 ms on `/v1/recommendations`).

- Check `recommender_model_rules`. A model with far more rules than usual (low thresholds) makes lookups slower.
- Check CPU throttling and the HPA (`kubectl get hpa`). Raise `maxReplicas` if it is pinned at the maximum.

## Many empty recommendations
`rate(recommender_empty_recommendations_total[1h])` rising means clients send tracks the model
doesn't know: the model is stale, or the thresholds prune too much. Check `trained_at` on `GET /v1/model`.
