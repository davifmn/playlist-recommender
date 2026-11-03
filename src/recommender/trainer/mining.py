"""Turn playlists into association rules with FP-Growth.

A playlist is a basket; its tracks are items. FP-Growth (mlxtend) finds track sets that
appear together in at least ``min_support`` of the playlists, and rules ``A -> C`` are kept
when ``confidence = support(A ∪ C) / support(A)`` is at least ``min_confidence``.

Memory is the bottleneck: a one-hot matrix of playlists × distinct tracks is huge. Two
things keep it small. Tracks rarer than ``min_support`` can never be part of a frequent
itemset, so they are dropped *before* encoding (usually most of the catalogue). The matrix
is also kept sparse.
"""

from __future__ import annotations

import io
import logging
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import pandas as pd

from recommender.core.model import Rule
from recommender.core.storage import ObjectStore

log = logging.getLogger(__name__)

REQUIRED_COLUMNS = {"pid", "track_uri"}
DENSE_CELL_LIMIT = 50_000_000  # ~50 MB as a bool matrix


class DatasetError(ValueError):
    pass


@dataclass(frozen=True)
class MiningParams:
    min_support: float = 0.1
    min_confidence: float = 0.5
    max_len: int = 3  # longest itemset; rules have at most max_len - 1 antecedents

    def validate(self) -> None:
        if not 0 < self.min_support <= 1:
            raise ValueError("min_support must be in (0, 1]")
        if not 0 < self.min_confidence <= 1:
            raise ValueError("min_confidence must be in (0, 1]")
        if self.max_len < 2:
            raise ValueError("max_len must be at least 2 to produce rules")


def read_datasets(store: ObjectStore, prefix: str = "datasets/") -> tuple[pd.DataFrame, list[str]]:
    keys = [k for k in store.list(prefix) if k.endswith(".csv")]
    if not keys:
        raise DatasetError(f"no CSV files under {prefix!r}")
    frames = []
    for key in keys:
        frame = pd.read_csv(io.BytesIO(store.get(key)), usecols=lambda c: c in REQUIRED_COLUMNS)
        missing = REQUIRED_COLUMNS - set(frame.columns)
        if missing:
            raise DatasetError(f"{key} is missing columns {sorted(missing)}")
        frames.append(frame)
        log.info("read %s (%d rows)", key, len(frame))
    return pd.concat(frames, ignore_index=True), keys


def build_baskets(frame: pd.DataFrame) -> list[list[str]]:
    frame = frame.dropna(subset=["pid", "track_uri"])
    grouped = frame.groupby("pid")["track_uri"].agg(lambda s: sorted(set(s)))
    return grouped.tolist()


def prune_rare_tracks(baskets: Sequence[Sequence[str]], min_support: float) -> list[list[str]]:
    counts = Counter(t for basket in baskets for t in basket)
    threshold = min_support * len(baskets)
    keep = {t for t, n in counts.items() if n >= threshold}
    return [[t for t in basket if t in keep] for basket in baskets]


def mine_rules(baskets: Sequence[Sequence[str]], params: MiningParams) -> tuple[list[Rule], dict]:
    from mlxtend.frequent_patterns import association_rules, fpgrowth
    from mlxtend.preprocessing import TransactionEncoder

    params.validate()
    if not baskets:
        raise DatasetError("dataset has no playlists")
    distinct = len({t for b in baskets for t in b})
    pruned = prune_rare_tracks(baskets, params.min_support)
    stats = {
        "playlists": len(baskets),
        "distinct_tracks": distinct,
        "tracks_after_pruning": len({t for b in pruned for t in b}),
    }
    if not stats["tracks_after_pruning"]:
        return [], {**stats, "frequent_itemsets": 0, "rules": 0}

    # Playlists left empty by pruning stay in the matrix: they still count in the support
    # denominator, exactly as if nothing had been pruned.
    encoder = TransactionEncoder().fit(pruned)
    cells = len(pruned) * len(encoder.columns_)
    if cells <= DENSE_CELL_LIMIT:
        onehot = pd.DataFrame(encoder.transform(pruned), columns=encoder.columns_)
    else:  # large catalogues: a sparse matrix trades some speed for a lot of memory
        matrix = encoder.transform(pruned, sparse=True)
        onehot = pd.DataFrame.sparse.from_spmatrix(matrix, columns=encoder.columns_)

    itemsets = fpgrowth(
        onehot, min_support=params.min_support, use_colnames=True, max_len=params.max_len
    )
    stats["frequent_itemsets"] = len(itemsets)
    if itemsets.empty or itemsets["itemsets"].map(len).max() < 2:
        return [], {**stats, "rules": 0}

    table = association_rules(
        itemsets,
        num_itemsets=len(baskets),
        metric="confidence",
        min_threshold=params.min_confidence,
        return_metrics=["support", "confidence", "lift"],
    )
    rules = [
        Rule(
            frozenset(row.antecedents),
            frozenset(row.consequents),
            float(row.support),
            float(row.confidence),
            float(row.lift),
        )
        for row in table.itertuples()
    ]
    rules.sort(key=lambda r: (-r.confidence, -r.lift, sorted(r.antecedents), sorted(r.consequents)))
    stats["rules"] = len(rules)
    return rules, stats


def fingerprint(store: ObjectStore, keys: Iterable[str]) -> str:
    import hashlib

    digest = hashlib.sha256()
    for key in sorted(keys):
        digest.update(key.encode())
        digest.update(hashlib.sha256(store.get(key)).digest())
    return digest.hexdigest()
