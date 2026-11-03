# ADR 2: Rules serialized as JSON lines, not pickle

**Status:** accepted

## Context
The course version pickled a pandas DataFrame. Loading a pickle executes code chosen by
whoever wrote the file, so anyone with write access to the bucket could run code inside
every API pod. Pickles also couple the reader to the writer's pandas version.

## Decision
Rules are gzip-compressed JSON lines `{"a": [...], "c": [...], "support", "confidence", "lift"}`,
with a manifest recording the schema version and the SHA-256 of the file. The API checks both
before loading.

## Consequences
- Loading untrusted data can't execute code. A tampered or truncated file is rejected.
- The API image doesn't need pandas, numpy, scipy or scikit-learn, so it is much smaller and starts faster.
- The format is readable by other languages and easy to inspect (`zcat | head`).
- Files are bigger than a binary format would be. That is acceptable at this size; Parquet would be the next step.
