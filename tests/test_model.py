import json

import pytest

from recommender.core.model import (
    LATEST_KEY,
    ModelError,
    Rule,
    RuleIndex,
    decode_rules,
    encode_rules,
    latest_version,
    load,
    publish,
)


def rule(a, c, conf, lift=1.0, support=0.1):
    return Rule(frozenset(a), frozenset(c), support, conf, lift)


RULES = [
    rule({"a"}, {"b"}, 0.9, 3.0),
    rule({"a", "c"}, {"d"}, 0.95, 4.0),
    rule({"c"}, {"b"}, 0.6, 2.0),
    rule({"x"}, {"y"}, 0.99, 5.0),
]


def test_rule_applies_only_when_all_antecedents_match():
    index = RuleIndex(RULES)
    assert [r.track for r in index.recommend(["a"])] == ["b"]
    # With "a" and "c", the two-antecedent rule fires too and outranks the others.
    assert [r.track for r in index.recommend(["a", "c"])] == ["d", "b"]


def test_best_rule_wins_and_inputs_are_never_recommended():
    index = RuleIndex(RULES)
    recs = index.recommend(["a", "c"])
    b = next(r for r in recs if r.track == "b")
    assert b.confidence == 0.9  # from a->b, not the weaker c->b
    assert not {"a", "c"} & {r.track for r in recs}


def test_limit_and_unknown_tracks():
    index = RuleIndex(RULES)
    assert len(index.recommend(["a", "c"], limit=1)) == 1
    assert index.recommend(["never-seen"]) == []


def test_encoding_round_trip_is_deterministic():
    blob = encode_rules(RULES)
    assert decode_rules(blob) == [Rule.from_json(r.to_json()) for r in RULES]
    assert encode_rules(RULES) == blob  # same rules, same bytes, same checksum


def test_publish_then_load(store):
    manifest = publish(
        store, RULES, params={"min_support": 0.1}, stats={"rules": 4}, dataset_sha256="x"
    )
    assert latest_version(store) == manifest.version
    loaded, index = load(store)
    assert loaded == manifest
    assert len(index) == 4


def test_latest_is_written_last(store, monkeypatch):
    written = []
    original_put = store.put
    monkeypatch.setattr(
        store, "put", lambda key, data, ct="": (written.append(key), original_put(key, data))[1]
    )
    publish(store, RULES, params={}, stats={}, dataset_sha256="x")
    assert written[-1] == LATEST_KEY
    assert written[0].endswith("rules.jsonl.gz")


def test_tampered_model_is_refused(store):
    manifest = publish(store, RULES, params={}, stats={}, dataset_sha256="x")
    store.put(f"models/{manifest.version}/rules.jsonl.gz", encode_rules(RULES[:1]))
    with pytest.raises(ModelError, match="checksum"):
        load(store)


def test_unknown_schema_is_refused(store):
    manifest = publish(store, RULES, params={}, stats={}, dataset_sha256="x")
    key = f"models/{manifest.version}/manifest.json"
    data = json.loads(store.get(key))
    data["schema_version"] = 99
    store.put(key, json.dumps(data).encode())
    with pytest.raises(ModelError, match="schema"):
        load(store)
