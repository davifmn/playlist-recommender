import pytest
from fastapi.testclient import TestClient

from recommender.api.app import create_app
from recommender.core.model import Rule, encode_rules, publish

RULES = [
    Rule(frozenset({"a"}), frozenset({"b"}), 0.2, 0.9, 3.0),
    Rule(frozenset({"a", "c"}), frozenset({"d"}), 0.1, 0.95, 4.0),
]


def make_client(store):
    return TestClient(create_app(store, start_poller=False))


def test_not_ready_until_a_model_exists(store):
    with make_client(store) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 503
        assert client.post("/v1/recommendations", json={"tracks": ["a"]}).status_code == 503


@pytest.fixture
def client(store):
    publish(store, RULES, params={"min_support": 0.1}, stats={"rules": 2}, dataset_sha256="abc")
    with make_client(store) as c:
        yield c


def test_recommendations(client):
    body = client.post("/v1/recommendations", json={"tracks": ["a", "c"], "limit": 5}).json()
    assert [t["track"] for t in body["tracks"]] == ["d", "b"]
    assert body["model"]["rules"] == 2


def test_legacy_route_keeps_the_course_contract(client):
    body = client.post("/api/recommend", json={"songs": ["a"]}).json()
    assert body["songs"] == ["b"]
    assert set(body) == {"songs", "version", "model_date"}


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"tracks": []},
        {"tracks": ["a"], "limit": 0},
        {"tracks": ["a"], "extra": 1},
        {"tracks": ["x"] * 501},
    ],
)
def test_input_validation(client, payload):
    assert client.post("/v1/recommendations", json=payload).status_code == 422


def test_model_info_and_readiness(client):
    info = client.get("/v1/model").json()
    assert info["params"] == {"min_support": 0.1} and info["dataset_sha256"] == "abc"
    assert client.get("/readyz").json()["model"] == info["version"]


def test_hot_reload_swaps_to_the_new_version(store, client):
    old = client.get("/v1/model").json()["version"]
    publish(store, RULES[:1], params={}, stats={}, dataset_sha256="def")
    assert client.app.state.models.refresh() is True
    new = client.get("/v1/model").json()["version"]
    assert new != old
    assert (
        client.post("/v1/recommendations", json={"tracks": ["a", "c"]}).json()["model"]["rules"]
        == 1
    )


def test_broken_new_version_keeps_the_old_model(store, client):
    old = client.get("/v1/model").json()["version"]
    manifest = publish(store, RULES[:1], params={}, stats={}, dataset_sha256="def")
    store.put(f"models/{manifest.version}/rules.jsonl.gz", encode_rules(RULES))  # corrupt it
    assert client.app.state.models.refresh() is False
    assert client.get("/v1/model").json()["version"] == old
    assert client.post("/v1/recommendations", json={"tracks": ["a"]}).status_code == 200


def test_metrics_endpoint(client):
    client.post("/v1/recommendations", json={"tracks": ["zzz"]})
    text = client.get("/metrics").text
    assert (
        'recommender_http_requests_total{method="POST",route="/v1/recommendations",status="200"}'
        in text
    )
    assert "recommender_empty_recommendations_total" in text
    assert "recommender_model_rules 2.0" in text
