# Phase 3: Task Image Construction and `task init`

Phase 3 turns a validated bundle and exact materialised Git tree into a
locally tagged Docker image, verifies that image under the configured runtime
policy, and writes the final immutable execution lock. It does not run hidden
tests, apply patches, invoke solvers, or implement Phase 4 validation.

## Docker boundary

Only the Docker CLI and local Docker daemon are supported. Docker runs without
a shell and with an isolated `HOME` and XDG configuration directory. Host
Docker contexts, `DOCKER_HOST`, credentials, cloud tokens, and unrelated
environment variables are not forwarded. Preflight records client/server
versions, daemon OS and architecture, and rootless mode.

Remote-daemon configuration is intentionally unavailable through `task init`.
The CLI uses the default local Docker socket visible in its isolated
environment.

## Phase 3 configuration policy

- Base images must be lowercase, credential-free references pinned as
  `repository@sha256:<64 hex>`.
- Platforms must be normalized `os/architecture[/variant]` values.
- Runtime users must be non-root numeric `uid:gid` values.
- Runtime working directories and tmpfs entries must be normalized safe
  container paths.
- Build argument names are identifiers. Names implying credentials or secrets
  (`TOKEN`, `PASSWORD`, `PASSWD`, `SECRET`, `API_KEY`, `PRIVATE_KEY`,
  `CREDENTIAL`, or `AUTH`) are rejected before Docker starts.
- Build argument values are passed only as Docker argv values. Stored commands
  and captured Docker output redact those values.

## Generated build context

Every build uses a new physical context containing exactly:

```text
Dockerfile
repo/
env/
```

`repo/` is copied from the verified source manifest, preserving exact content,
canonical executable modes, and safe internal symlinks. `env/` contains only
Phase 1 digest-covered files from the configured environment context.
Runtime-owned `.task`, `artifacts`, and evaluation/hidden content are not
staged from the bundle.

Custom-Dockerfile mode copies the configured Dockerfile without modification.
Its stable contract is that baseline source is available at `repo/`,
environment context is available at `env/`, and the resulting image must
contain the baseline repository at `/opt/task/repo`.

Base-image mode generates:

```Dockerfile
FROM <validated digest reference>
COPY repo/ /opt/task/repo/
WORKDIR <configured runtime working directory>
```

The context manifest records sorted file/symlink identity. Context,
Dockerfile, repository, and environment-context digests are persisted as
artifacts. Temporary contexts are removed after every outcome unless
`--keep-build-context` is selected, in which case the context is retained
under `.task/build-contexts/<command-id>/`.

## Build and image identity

The deterministic local reference is derived from the normalized task ID,
bundle digest, and selected platform:

```text
task-bundle/<task>:<bundle-prefix>-<platform>
```

Docker receives explicit platform, network mode, cache policy, timeout, sorted
build arguments, and required OCI-style labels for task ID, bundle digest,
source commit/digest, build-context digest, and CLI version.

After a successful build, `docker image inspect` is authoritative. The CLI
records and validates:

- immutable image ID;
- local tag resolution;
- repository digests when present;
- OS, architecture, and variant;
- creation metadata, size, configured user, and working directory;
- every required identity label.

An actual-platform mismatch is a hard failure. Host-versus-requested
architecture is reported as an emulation warning.

Declared `Config.Volumes` entries are normalized as absolute POSIX container
paths. A volume at, above, or below `/opt/task/repo` fails with
`IMAGE_SOURCE_VOLUME_CONFLICT` before any source-verification, smoke, or runtime
container is created. Component comparison avoids treating unrelated
`/opt/task/repository` or `/opt/task/repo-cache` paths as conflicts.

## Complete image/source equality

After inspection, the CLI creates a stopped container from the immutable image
ID and exports `/opt/task/repo` into a controlled temporary host directory.
The host walks the export without following symlinks and rejects `.git`,
special files, hard links, unsafe paths/targets, case collisions, and unsupported
types. It compares every expected and actual entry by relative path, type,
regular-file SHA-256/size/executable mode, and symlink target.

The exported manifest must have the locked normalized source digest. Trusted
raw Git plumbing then reconstructs `100644`, `100755`, and `120000` entries and
requires the resulting tree SHA to equal the verified source tree. This uses no
working-tree filters, attributes, LFS, EOL conversion, or in-image integrity
command. Differences fail with `IMAGE_SOURCE_MISMATCH` and bounded added,
removed, changed, mode-changed, and type-changed path lists.

## Restricted smoke check

No task tests run during Phase 3. The smoke check creates a short-lived
container from the immutable image ID with:

- configured non-root numeric user;
- network `none`;
- read-only root filesystem;
- CPU, memory, and PID limits;
- all capabilities dropped;
- `no-new-privileges`;
- only configured tmpfs mounts;
- no host mounts and no Docker socket.

It verifies `/opt/task/repo`, the configured working directory, and the
SHA-256 of a known source file from the source manifest. Container removal is
attempted in `finally` on success, failure, timeout, and interruption.

## Bundle lock and freshness

The final lock is atomically written only after source verification, context
construction, Docker build, inspection, volume-policy validation, full exported
source/manifest/raw-tree equality, platform validation, and smoke success:

```text
.task/bundle.lock.json
```

The strict versioned lock records bundle/provenance identity, requested and
resolved source identities, tree and materialised-source digests, environment
and context identity, immutable image ID and tag, actual platform, runtime
policy digest, and evaluation-input digests.

Repeated `task init` inspects the recorded tag and returns
`already_initialized` without fetching or rebuilding when bundle, source
request, runtime policy, image reference, image ID, and platform remain
current. Any mismatch requires `--rebuild`. A failed rebuild does not replace
the previous lock.

## CLI

```bash
task init BUNDLE [--rebuild] [--no-cache] [--platform PLATFORM]
                 [--keep-build-context] [--json] [--no-colour]
```

No Phase 4 validation, solver, caching, alternate engine, or remote-Docker
options are exposed.

## Observability

Each invocation receives a `cmd_<uuid>` ID and writes SQLite command/events
plus artifacts under:

```text
artifacts/<safe-task-id>/<command-id>/
```

Artifacts include command/options, bundle/source snapshots and manifests,
source fetch logs, Docker environment, context identity, redacted build and
smoke commands, stdout/stderr, image inspection, smoke result, lock copy, and
JSON/Markdown reports. Expected domain failures use stable exit codes and do
not emit Python tracebacks.

## Explicitly deferred

- baseline and golden validation;
- hidden test injection;
- solvers and candidate patches;
- image/source caching;
- alternate container engines;
- remote Docker daemons;
- private Git authentication and submodules.
