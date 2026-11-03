"""Trainer entry point, run as a Kubernetes Job/CronJob or locally.

    recommender-train run            mine rules from datasets/ and publish a new model
    recommender-train seed-demo      upload synthetic playlists to datasets/ (dev/CI only)

Exit codes: 0 published (or nothing to do), 1 the run failed, 2 bad configuration.
Configuration comes from the environment (RECSYS_*), so the same image works in every
environment and the Helm chart only changes variables.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

from recommender.core.model import publish
from recommender.core.storage import StoreError, store_from_env

from .mining import (
    DatasetError,
    MiningParams,
    build_baskets,
    fingerprint,
    mine_rules,
    read_datasets,
)
from .sample_data import generate_csv

log = logging.getLogger("recommender.trainer")


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def run(args: argparse.Namespace) -> int:
    store = store_from_env()
    params = MiningParams(
        min_support=args.min_support or _env_float("RECSYS_MIN_SUPPORT", 0.1),
        min_confidence=args.min_confidence or _env_float("RECSYS_MIN_CONFIDENCE", 0.5),
        max_len=args.max_len or int(os.environ.get("RECSYS_MAX_LEN", 3)),
    )
    started = time.perf_counter()
    frame, keys = read_datasets(store, args.prefix)
    baskets = build_baskets(frame)
    rules, stats = mine_rules(baskets, params)
    if not rules and not args.allow_empty:
        log.error("no rules found; lower min_support/min_confidence (stats=%s)", stats)
        return 1
    stats["training_seconds"] = round(time.perf_counter() - started, 2)
    manifest = publish(
        store,
        rules,
        params=vars(params),
        stats=stats,
        dataset_sha256=fingerprint(store, keys),
    )
    # One JSON line, easy to pick up from `kubectl logs` or a log pipeline.
    print(json.dumps({"event": "model_published", "version": manifest.version, **stats}))
    return 0


def seed_demo(args: argparse.Namespace) -> int:
    store = store_from_env()
    if args.create_bucket and hasattr(store, "ensure_bucket") and store.ensure_bucket():
        log.info("created bucket %s", store.bucket)
    key = f"{args.prefix}synthetic_playlists.csv"
    if store.list(key) and not args.force:
        log.info("%s already exists; skipping (use --force to overwrite)", key)
        return 0
    store.put(key, generate_csv(args.playlists, seed=args.seed), "text/csv")
    print(json.dumps({"event": "demo_data_uploaded", "key": key, "playlists": args.playlists}))
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"), format="%(levelname)s %(message)s"
    )
    parser = argparse.ArgumentParser(prog="recommender-train")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="mine rules and publish a new model version")
    p_run.add_argument("--prefix", default="datasets/")
    p_run.add_argument("--min-support", type=float)
    p_run.add_argument("--min-confidence", type=float)
    p_run.add_argument("--max-len", type=int)
    p_run.add_argument("--allow-empty", action="store_true", help="publish even with 0 rules")

    p_seed = sub.add_parser("seed-demo", help="upload synthetic playlists (dev/CI only)")
    p_seed.add_argument("--prefix", default="datasets/")
    p_seed.add_argument("--playlists", type=int, default=600)
    p_seed.add_argument("--seed", type=int, default=7)
    p_seed.add_argument("--force", action="store_true")
    p_seed.add_argument("--create-bucket", action="store_true", help="create it if missing")

    args = parser.parse_args(argv)
    try:
        return run(args) if args.command == "run" else seed_demo(args)
    except (StoreError, DatasetError, ValueError) as exc:
        log.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
