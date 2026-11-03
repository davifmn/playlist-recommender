# ADR 1: Model artifacts in object storage, published with an atomic pointer

**Status:** accepted

## Context
The course version wrote the model to a ReadWriteMany PersistentVolumeClaim shared by the
training Job and the API pods. That needs an RWX storage class, ties pods to the volume's
availability zone, keeps no history and gives readers no way to tell a finished file from
one still being written.

## Decision
Store datasets and models in an S3 bucket:

- each training run writes `models/<version>/rules.jsonl.gz` and `models/<version>/manifest.json`;
- it then writes `models/LATEST`, last, containing the version name;
- the API polls `LATEST`, downloads the version, verifies the checksum in the manifest and swaps it in memory.

Versions are `<UTC timestamp>-<hash prefix of the rules>`.

## Consequences
- Readers see the old model or the complete new one, never a partial write.
- Rollback means writing an older version name into `LATEST`. Old versions can expire with an S3 lifecycle rule.
- Pods are stateless and can run in any zone. Scaling the API needs no shared volume.
- Any S3-compatible store works (S3, GCS interop, MinIO, Ceph). CI uses an in-memory emulator.
- New models reach the API after up to `RECSYS_POLL_SECONDS`. An S3 event notification could push instead, at the cost of more infrastructure.
