# ADR 3: Retrain on a schedule and on deploy

**Status:** accepted

## Context
Training ran only as an Argo CD sync hook, so the model was refreshed only when someone
deployed. New playlist data needs to reach the model without a code change.

## Decision
- A CronJob retrains nightly (`concurrencyPolicy: Forbid`, `activeDeadlineSeconds`, history limits).
- A Job also trains on every deploy (an Argo CD `Sync` hook, or one Job per Helm release
  revision), so a brand-new environment becomes ready without waiting for the night.
- The trainer exits non-zero and does **not** publish when it finds no rules, unless
  `--allow-empty` is given. A bad threshold therefore never replaces a good model.

## Consequences
- Data freshness doesn't depend on deploy frequency.
- Two trainings never race. The atomic pointer (ADR 1) makes the last successful publish win.
- Failures are visible as failed Jobs (alert `RecommenderTrainingFailed`).
