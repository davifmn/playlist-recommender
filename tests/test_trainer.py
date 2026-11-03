import json

import pandas as pd
import pytest

from recommender.core.model import latest_version, load
from recommender.trainer import cli
from recommender.trainer.mining import (
    DatasetError,
    MiningParams,
    build_baskets,
    mine_rules,
    prune_rare_tracks,
    read_datasets,
)
from recommender.trainer.sample_data import GENRES, anchors, catalogue, generate_rows


def test_baskets_group_unique_tracks_per_playlist():
    frame = pd.DataFrame({"pid": [1, 1, 1, 2], "track_uri": ["a", "b", "a", "c"]})
    assert build_baskets(frame) == [["a", "b"], ["c"]]


def test_pruning_drops_tracks_below_min_support():
    baskets = [["a", "b"], ["a"], ["a", "c"], ["a"]]
    assert prune_rare_tracks(baskets, 0.5) == [["a"], ["a"], ["a"], ["a"]]


def test_finds_the_planted_patterns():
    baskets = build_baskets(pd.DataFrame(generate_rows(600, seed=7)))
    rules, stats = mine_rules(baskets, MiningParams(min_support=0.1, min_confidence=0.5))
    tracks = catalogue()
    for genre in GENRES:
        group = set(anchors(genre, tracks))
        assert any(r.antecedents | r.consequents <= group for r in rules), genre
    assert stats["playlists"] == 600
    assert stats["rules"] == len(rules) > 0
    # Rules never mix genres: anchors only co-occur inside their own genre.
    groups = [set(anchors(g, tracks)) for g in GENRES]
    assert all(any(r.antecedents | r.consequents <= g for g in groups) for r in rules)


def test_pruned_playlists_still_count_in_support():
    # "x" is in 2 of 4 playlists: support 0.5 even though two playlists become empty.
    baskets = [["x", "y"], ["x", "y"], ["z"], ["w"]]
    rules, _ = mine_rules(baskets, MiningParams(min_support=0.5, min_confidence=0.5))
    assert {r.support for r in rules} == {0.5}


def test_strict_thresholds_produce_no_rules():
    baskets = build_baskets(pd.DataFrame(generate_rows(100, seed=1)))
    rules, stats = mine_rules(baskets, MiningParams(min_support=0.99, min_confidence=0.99))
    assert rules == [] and stats["rules"] == 0


@pytest.mark.parametrize(
    "bad", [MiningParams(min_support=0), MiningParams(min_confidence=2), MiningParams(max_len=1)]
)
def test_invalid_params(bad):
    with pytest.raises(ValueError):
        bad.validate()


def test_dataset_validation(store):
    with pytest.raises(DatasetError, match="no CSV"):
        read_datasets(store)
    store.put("datasets/bad.csv", b"pid,name\n1,x\n")
    with pytest.raises(DatasetError, match="missing columns"):
        read_datasets(store)


def test_cli_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RECSYS_STORE", "local")
    monkeypatch.setenv("RECSYS_LOCAL_ROOT", str(tmp_path))
    assert cli.main(["seed-demo", "--playlists", "400"]) == 0
    assert cli.main(["seed-demo"]) == 0  # idempotent: does not overwrite
    assert cli.main(["run"]) == 0
    event = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert event["event"] == "model_published" and event["rules"] > 0

    from recommender.core.storage import LocalStore

    store = LocalStore(tmp_path)
    manifest, index = load(store)
    assert manifest.version == latest_version(store) == event["version"]
    assert manifest.params["min_support"] == 0.1
    assert len(index) == event["rules"]


def test_cli_fails_when_nothing_is_found(tmp_path, monkeypatch):
    monkeypatch.setenv("RECSYS_STORE", "local")
    monkeypatch.setenv("RECSYS_LOCAL_ROOT", str(tmp_path))
    cli.main(["seed-demo", "--playlists", "50"])
    assert cli.main(["run", "--min-support", "0.99"]) == 1


def test_cli_reports_configuration_errors(monkeypatch):
    monkeypatch.setenv("RECSYS_STORE", "s3")
    monkeypatch.delenv("RECSYS_BUCKET", raising=False)
    assert cli.main(["run"]) == 2
