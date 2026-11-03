"""The model artifact: association rules, how they are stored, and how they are served.

Rules are stored as gzip-compressed JSON lines, never as pickle. Unpickling runs arbitrary
code, so a pickle in a shared bucket is a remote-code-execution vector. JSON is inert,
portable to other languages, and diff-able when debugging a bad model.

Publishing is atomic: rules and manifest are written under a new version prefix first,
and only then is ``models/LATEST`` switched to that version. The API therefore sees
either the old model or the complete new one, never a half-written mix. The manifest
records the SHA-256 of the rules file, and loading verifies it.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from .storage import ObjectStore

LATEST_KEY = "models/LATEST"


class ModelError(RuntimeError):
    pass


@dataclass(frozen=True)
class Rule:
    antecedents: frozenset[str]
    consequents: frozenset[str]
    support: float
    confidence: float
    lift: float

    def to_json(self) -> dict:
        return {
            "a": sorted(self.antecedents),
            "c": sorted(self.consequents),
            "support": round(self.support, 6),
            "confidence": round(self.confidence, 6),
            "lift": round(self.lift, 6),
        }

    @classmethod
    def from_json(cls, data: dict) -> Rule:
        return cls(
            frozenset(data["a"]),
            frozenset(data["c"]),
            float(data["support"]),
            float(data["confidence"]),
            float(data["lift"]),
        )


@dataclass(frozen=True)
class Manifest:
    version: str
    trained_at: str
    params: dict
    stats: dict
    dataset_sha256: str
    rules_sha256: str
    schema_version: int = 1

    @classmethod
    def from_json(cls, data: dict) -> Manifest:
        return cls(**data)


@dataclass(frozen=True)
class Recommendation:
    track: str
    confidence: float
    lift: float


@dataclass
class RuleIndex:
    """Inverted index from track to the rules whose antecedent contains it.

    A rule applies when *all* of its antecedent tracks are in the user's input. Instead of
    testing every rule (what the original course version did with a pandas ``apply`` on
    every request), we only touch rules that mention at least one input track and count how
    many of their antecedents matched. The cost grows with the matching rules, not the model.
    """

    rules: list[Rule]
    _by_track: dict[str, list[int]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        index: dict[str, list[int]] = defaultdict(list)
        for i, rule in enumerate(self.rules):
            for track in rule.antecedents:
                index[track].append(i)
        self._by_track = dict(index)

    def __len__(self) -> int:
        return len(self.rules)

    @property
    def tracks(self) -> set[str]:
        return set(self._by_track) | {t for r in self.rules for t in r.consequents}

    def recommend(self, tracks: Iterable[str], limit: int = 10) -> list[Recommendation]:
        given = set(tracks)
        matched: dict[int, int] = defaultdict(int)
        for track in given:
            for rule_id in self._by_track.get(track, ()):
                matched[rule_id] += 1

        best: dict[str, Recommendation] = {}
        for rule_id, hits in matched.items():
            rule = self.rules[rule_id]
            if hits != len(rule.antecedents):
                continue
            for track in rule.consequents:
                if track in given:
                    continue
                current = best.get(track)
                candidate = Recommendation(track, rule.confidence, rule.lift)
                if current is None or (candidate.confidence, candidate.lift) > (
                    current.confidence,
                    current.lift,
                ):
                    best[track] = candidate
        ranked = sorted(best.values(), key=lambda r: (-r.confidence, -r.lift, r.track))
        return ranked[:limit]


# --- serialization ---------------------------------------------------------------------


def encode_rules(rules: Sequence[Rule]) -> bytes:
    lines = "\n".join(json.dumps(r.to_json(), sort_keys=True) for r in rules)
    # mtime=0 makes the gzip output deterministic, so the same rules give the same checksum.
    return gzip.compress(lines.encode(), mtime=0)


def decode_rules(blob: bytes) -> list[Rule]:
    text = gzip.decompress(blob).decode()
    return [Rule.from_json(json.loads(line)) for line in text.splitlines() if line.strip()]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def new_version(rules_blob: bytes, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    return f"{now:%Y%m%dT%H%M%SZ}-{sha256(rules_blob)[:8]}"


def publish(
    store: ObjectStore,
    rules: Sequence[Rule],
    *,
    params: dict,
    stats: dict,
    dataset_sha256: str,
    now: datetime | None = None,
) -> Manifest:
    """Write a new model version and point ``models/LATEST`` at it (last step)."""
    now = now or datetime.now(UTC)
    blob = encode_rules(rules)
    version = new_version(blob, now)
    manifest = Manifest(
        version=version,
        trained_at=now.isoformat(timespec="seconds"),
        params=params,
        stats=stats,
        dataset_sha256=dataset_sha256,
        rules_sha256=sha256(blob),
    )
    store.put(f"models/{version}/rules.jsonl.gz", blob, "application/gzip")
    store.put(
        f"models/{version}/manifest.json",
        json.dumps(asdict(manifest), indent=2, sort_keys=True).encode(),
        "application/json",
    )
    store.put(LATEST_KEY, version.encode(), "text/plain")
    return manifest


def latest_version(store: ObjectStore) -> str:
    return store.get(LATEST_KEY).decode().strip()


def load(store: ObjectStore, version: str | None = None) -> tuple[Manifest, RuleIndex]:
    version = version or latest_version(store)
    manifest = Manifest.from_json(json.loads(store.get(f"models/{version}/manifest.json")))
    if manifest.schema_version != 1:
        raise ModelError(f"unsupported model schema {manifest.schema_version}")
    blob = store.get(f"models/{version}/rules.jsonl.gz")
    if sha256(blob) != manifest.rules_sha256:
        raise ModelError(f"checksum mismatch for model {version}: refusing to load it")
    return manifest, RuleIndex(decode_rules(blob))
