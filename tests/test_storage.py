import boto3
import pytest
from moto import mock_aws

from recommender.core.storage import LocalStore, ObjectNotFound, S3Store, StoreError, store_from_env


@pytest.fixture
def s3_store():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="recsys")
        yield S3Store("recsys", client=client)


@pytest.fixture(params=["local", "s3"])
def any_store(request, tmp_path, s3_store):
    return LocalStore(tmp_path) if request.param == "local" else s3_store


def test_put_get_list(any_store):
    any_store.put("datasets/a.csv", b"1")
    any_store.put("datasets/b.csv", b"2")
    any_store.put("models/LATEST", b"v1")
    assert any_store.get("datasets/a.csv") == b"1"
    assert any_store.list("datasets/") == ["datasets/a.csv", "datasets/b.csv"]


def test_missing_object(any_store):
    with pytest.raises(ObjectNotFound):
        any_store.get("models/LATEST")


@pytest.mark.parametrize("key", ["", "/etc/passwd", "models/../../secret"])
def test_rejects_path_traversal(any_store, key):
    with pytest.raises(StoreError):
        any_store.get(key)


def test_store_from_env(tmp_path):
    assert isinstance(
        store_from_env({"RECSYS_STORE": "local", "RECSYS_LOCAL_ROOT": str(tmp_path)}), LocalStore
    )
    with pytest.raises(StoreError, match="RECSYS_BUCKET"):
        store_from_env({"RECSYS_STORE": "s3"})
    with pytest.raises(StoreError, match="unknown"):
        store_from_env({"RECSYS_STORE": "ftp"})


def test_ensure_bucket_is_idempotent():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        store = S3Store("fresh", client=client)
        assert store.ensure_bucket() is True
        assert store.ensure_bucket() is False
        store.put("x", b"1")
        assert store.get("x") == b"1"
