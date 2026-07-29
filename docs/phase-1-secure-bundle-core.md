# Phase 1: Secure Bundle Core

Phase 1 processes a task bundle without executing bundle content or performing
network, Git, Docker, solver, or test operations.

## Loading and path security

`load_bundle()` reads `task.yaml` with a safe YAML loader that rejects duplicate
keys, custom tags, anchors, aliases, and merge keys, then validates it through
the strict immutable task model.
Every configured bundle path must be relative, exist inside the resolved bundle
root, and have the expected regular-file or directory type.

Symlinks are rejected in all digest-covered inputs. This deliberately simple
policy prevents both escaping links and host-dependent link interpretation.
Sockets, FIFOs, devices, and other special filesystem objects are rejected.

## Canonical configuration and digest

Canonical configuration is UTF-8 JSON produced from the validated model with
sorted keys, stable separators, and explicit `null` values. YAML formatting,
comments, indentation, and key order therefore have no effect.

The input manifest records each relevant regular file using its normalized
bundle-relative POSIX path, canonical executable mode (`0755` or `0644`), size,
and SHA-256 content digest. Entries are sorted by path. The final bundle digest
hashes a framed canonical document containing the canonical-configuration hash
and manifest.

Every file beneath the conventional `evaluation/` tree is considered
execution-relevant because its runner may invoke sibling harness, parser,
fixture, or helper files. Public files explicitly named by the schema, the
Dockerfile, and the environment context are also covered.
Base-image references, selectors, provenance, platform, and build arguments are
covered by canonical configuration.

Generated state is excluded from non-evaluation directory traversal:

- `.task/`, `artifacts/`, and `__pycache__/`
- Python bytecode
- SQLite/database files
- generated build-context directories
- atomic snapshot temporary files

Explicit files and files beneath `evaluation/` take precedence over filename
suffix exclusions. For example, `evaluation/fixtures/baseline.db` is included.
Runtime-owned `.task/`, `artifacts/`, and generated-context paths remain
forbidden as explicit inputs.

## Provenance and snapshots

Optional dataset provenance is fixed input from `task.yaml`; its source digest
is validated and its timestamp normalized to UTC.

`BundleSnapshot` records the Phase 1 bundle digest, canonical hash, manifest,
evaluation-input hashes, provenance, CLI version, and creation timestamp at
`.task/bundle.snapshot.json`. It is not the future `BundleLock`: repository
commit resolution, Git tree identity, task image identity, and runtime policy
are intentionally unavailable until later phases.

Snapshots are written through a temporary file in the destination directory,
flushed, fsynced, and atomically replaced. Stale comparison returns both
digests and reports added, removed, content-changed, mode-changed, and
configuration-only inputs.
