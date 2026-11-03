"""Object storage abstraction: the same code runs on a laptop, on S3 or on an S3-compatible store.

Datasets and model artifacts live in one bucket, laid out like a small data lake::

    datasets/<name>.csv                  input playlists (any number of CSV files)
    models/<version>/rules.jsonl.gz      mined association rules
    models/<version>/manifest.json       metadata, parameters, checksums
    models/LATEST                        name of the version the API should serve

``LocalStore`` is used in tests and for running without any cloud. ``S3Store`` talks to AWS
S3, or to any S3-compatible endpoint (moto server in CI, MinIO/SeaweedFS/Ceph on premises,
GCS through its S3 interoperability API) when ``endpoint_url`` is set.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol


class StoreError(RuntimeError):
    pass


class ObjectNotFound(StoreError):
    pass


class ObjectStore(Protocol):
    def get(self, key: str) -> bytes: ...

    def put(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> None: ...

    def list(self, prefix: str) -> list[str]: ...


def _check_key(key: str) -> str:
    if not key or key.startswith("/") or ".." in key.split("/"):
        raise StoreError(f"invalid object key {key!r}")
    return key


class LocalStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / _check_key(key)

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise ObjectNotFound(key)
        return path.read_bytes()

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)  # readers never see a half-written object

    def list(self, prefix: str) -> list[str]:
        base = self.root
        if not base.exists():
            return []
        keys = [p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file()]
        return sorted(k for k in keys if k.startswith(prefix) and not k.endswith(".tmp"))


class S3Store:
    def __init__(self, bucket: str, *, endpoint_url: str | None = None, client=None) -> None:
        import boto3  # imported lazily so LocalStore users don't need AWS libraries

        self.bucket = bucket
        self.client = client or boto3.client("s3", endpoint_url=endpoint_url or None)

    def get(self, key: str) -> bytes:
        from botocore.exceptions import ClientError

        try:
            return self.client.get_object(Bucket=self.bucket, Key=_check_key(key))["Body"].read()
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "404"}:
                raise ObjectNotFound(key) from None
            raise StoreError(f"s3://{self.bucket}/{key}: {exc}") from exc

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self.client.put_object(
            Bucket=self.bucket, Key=_check_key(key), Body=data, ContentType=content_type
        )

    def ensure_bucket(self) -> bool:
        """Create the bucket if it does not exist (dev/CI emulators only; prod uses Terraform)."""
        from botocore.exceptions import ClientError

        try:
            self.client.head_bucket(Bucket=self.bucket)
            return False
        except ClientError:
            self.client.create_bucket(Bucket=self.bucket)
            return True

    def list(self, prefix: str) -> list[str]:
        keys: list[str] = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys += [obj["Key"] for obj in page.get("Contents", [])]
        return sorted(keys)


def store_from_env(env: dict[str, str] | None = None) -> ObjectStore:
    """Build the store from RECSYS_* environment variables (12-factor configuration)."""
    env = dict(os.environ if env is None else env)
    kind = env.get("RECSYS_STORE", "local").lower()
    if kind == "local":
        return LocalStore(env.get("RECSYS_LOCAL_ROOT", "./data"))
    if kind == "s3":
        bucket = env.get("RECSYS_BUCKET")
        if not bucket:
            raise StoreError("RECSYS_BUCKET is required when RECSYS_STORE=s3")
        return S3Store(bucket, endpoint_url=env.get("RECSYS_S3_ENDPOINT_URL"))
    raise StoreError(f"unknown RECSYS_STORE {kind!r} (expected 'local' or 's3')")
