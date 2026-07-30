# Task Bundle CLI

## Final End-to-End Technical Design

**Version:** 1.1
**Status:** Final implementation baseline
**Implementation language:** Python 3.12
**Execution runtime:** Docker
**Persistence:** SQLite
**Primary objective:** Safely, reproducibly and observably evaluate coding-agent solutions against hidden benchmark tests across arbitrary coding repositories.

---

# 1. Executive summary

Modern coding-agent benchmarks generally define a task through:

1. A repository at an exact commit.
2. A public problem statement.
3. A known-correct code patch.
4. Hidden tests that verify the intended behaviour.
5. An execution environment in which the repository can be built and tested.

The Task Bundle CLI will package these elements into a portable task bundle and expose four primary commands:

```bash
task init
task validate
task run
task show
```

The system will enable task developers to:

* Materialise a repository at an exact Git commit.
* Construct a task-specific immutable container image.
* Verify that the repository and evaluation harness work.
* Validate baseline and golden-patch behaviour.
* Run a solver without exposing hidden tests.
* Extract the solver’s filesystem changes into a portable Git patch.
* Evaluate that patch in a fresh isolated container.
* Report test-level `PASS_TO_PASS` and `FAIL_TO_PASS` results.
* Preserve logs, patches, environment metadata and structured reports.
* Retrieve a previous command and its complete lifecycle using a stable command ID.

The most important architectural invariant is:

> The solver and evaluator always run in separate short-lived containers created from the same immutable task image.

The solver receives only:

* The baseline repository.
* The public problem statement.
* Public requirements and interface notes.
* Tests already present at the baseline commit.
* An explicitly staged solver implementation, when required.

The solver never receives:

* The golden patch.
* The hidden test patch.
* Hidden test selectors.
* Golden-validation results.
* Evaluator harness files.
* The bundle root.
* Previous run artefacts.

After the solver finishes, its workspace is copied out and converted into a candidate patch using a fresh trusted Git checkout on the host. The solver container is then destroyed.

A separate candidate evaluator receives:

* The pristine baseline repository.
* The candidate patch.
* The hidden test patch.
* The trusted evaluation harness.
* A structured evaluation plan.

This design provides:

* Correct baseline-versus-candidate semantics.
* Strong pre-solution hidden-test isolation.
* Clean candidate comparison.
* Explicit trust boundaries.
* Cross-language repository support.
* Reproducible execution identity.
* Complete lifecycle observability.
* Clear classification of solver, patch, test and infrastructure failures.

Implementation will proceed through strict vertical-slice gates. Optional features will not begin until the full mandatory workflow passes on both a synthetic task and a real SWE-bench Pro task.

---

# 2. Problem definition

A benchmark task contains the following logical components.

## 2.1 Baseline repository

The source repository checked out at an exact Git commit before the intended fix has been applied.

The commit must be immutable and verified. Branches and tags may be used for discovery but not as the final source identity.

## 2.2 Public problem statement

The description of the behaviour the solver must implement or repair.

It may include:

* Problem description.
* Functional requirements.
* Interface requirements.
* Constraints.
* Public examples.

It must not include information derived exclusively from hidden tests.

## 2.3 Golden patch

A known-correct code change that solves the task.

The golden patch is used only during task validation. It is never exposed to the solver.

## 2.4 Hidden test patch

A patch that creates or modifies tests used to evaluate the task.

It is injected only after the baseline, golden or candidate workspace has been created.

## 2.5 `PASS_TO_PASS`

Tests representing behaviour that must remain correct.

They must:

```text
Pass on the baseline
Pass after the golden patch
Pass after a correct candidate patch
```

A candidate that solves the requested issue but breaks a `PASS_TO_PASS` test is unresolved.

## 2.6 `FAIL_TO_PASS`

Tests representing behaviour repaired or introduced by the task.

They must:

```text
Produce the explicitly configured non-passing baseline result
Pass after the golden patch
Pass after a correct candidate patch
```

The expected baseline result is task-specific and explicitly represented in the bundle.

## 2.7 Candidate patch

The final code change produced from a solver workspace.

The evaluator receives this patch—not the solver container’s mutable filesystem.

---

# 3. Design goals

## 3.1 Correctness

The system must accurately distinguish:

* Intended baseline test failure.
* Test-level runtime error.
* Global test collection failure.
* Broken environment.
* Incorrect golden patch.
* Partial candidate success.
* Candidate regression.
* Solver failure.
* Patch-policy violation.
* Evaluation infrastructure failure.

A non-zero process exit code alone is insufficient. Every requested test selector must be represented in structured output.

## 3.2 Hidden-test isolation

Before the candidate patch is finalised, the solver must not have access to:

* Hidden test files.
* Hidden test selectors.
* Golden patch content.
* Evaluator scripts.
* Golden validation reports.
* Prior candidate results.

## 3.3 Repository independence

The orchestration layer must not assume:

* Python.
* pytest.
* A specific package manager.
* A particular test framework.
* A particular dependency layout.
* A particular build tool.

Repository-specific behaviour is expressed through:

* An explicit environment contract.
* A task-specific evaluation runner.
* A normalised result schema.

## 3.4 Reproducible execution identity

Every command must record enough information to identify exactly what was executed:

* Repository URL.
* Commit SHA.
* Git tree SHA.
* Bundle input digest.
* Task image ID.
* Image platform.
* Runtime security policy.
* Evaluation selectors.
* Patch hashes.
* Solver context digest.
* CLI version.

The system does not claim universal bit-identical image rebuilding from mutable upstream sources.

## 3.5 Isolation

Untrusted solver and candidate code must run with:

* Non-root users.
* Runtime network disabled by default.
* No Docker socket.
* No host credentials.
* No arbitrary host mounts.
* CPU, memory and PID limits.
* Wall-clock timeouts.
* Dropped Linux capabilities.
* `no-new-privileges`.
* Dedicated writable workspaces.

## 3.6 Observability

Every command must produce:

* A stable command ID.
* Structured lifecycle events.
* stdout and stderr.
* Patch-application logs.
* Environment metadata.
* Test-level results.
* JSON and Markdown reports.
* Database records linking all generated artefacts.

## 3.7 Clear task-author experience

The CLI must provide:

* Strict but understandable bundle validation.
* Meaningful errors.
* Readable progress output.
* Machine-readable output for CI.
* Direct links or paths to relevant artefacts.
* Distinct infrastructure and evaluation outcomes.

---

# 4. Non-goals

The first version will not attempt to:

* Build a complete autonomous coding agent.
* Automatically infer every repository’s environment.
* Guarantee cryptographic hidden-test secrecy during test execution.
* Provide microVM-grade isolation.
* Run Kubernetes or distributed workers.
* Support a multi-user web platform.
* Support PostgreSQL or remote databases.
* Support remote artefact storage.
* Support private dependency credentials.
* Forward host secrets into builds.
* Reimplement SWE-Agent or OpenHands.
* Guarantee protection against every container-runtime vulnerability.
* Guarantee byte-identical rebuilds from mutable package registries.
* Support submodule changes in candidate patches.
* Support arbitrary special filesystem objects in solver output.

These may be added later without changing the core lifecycle.

---

# 5. Core correctness invariants

## 5.1 Exact-source invariant

Every phase begins from the repository at the exact resolved commit declared in the lockfile.

```text
actual HEAD == locked commit
actual tree SHA == locked tree SHA
```

## 5.2 Task-image invariant

Every execution uses a CLI-produced task image containing the verified repository snapshot.

This remains true whether the task uses:

* A custom Dockerfile.
* A pinned base environment image.

A configured image is never treated as an opaque final task image.

## 5.3 Baseline invariant

After applying the hidden test patch to a fresh baseline workspace:

* Every `PASS_TO_PASS` selector must report `passed`.
* Every `FAIL_TO_PASS` selector must report one of its explicitly configured baseline statuses.
* The evaluation harness must complete meaningfully.
* Every requested selector must have exactly one mapped result.

## 5.4 Golden invariant

After applying the golden patch and hidden test patch to a fresh workspace:

```text
All PASS_TO_PASS tests pass
All FAIL_TO_PASS tests pass
No requested selector is missing
No infrastructure failure occurs
```

If this invariant fails, the task is invalid.

## 5.5 Solver-secrecy invariant

Before solver completion, the solver cannot access:

```text
golden.patch
test.patch
hidden selectors
evaluation/input
evaluation/harness
previous reports
bundle root
SQLite database
host artefact directory
```

## 5.6 Clean-candidate invariant

Candidate evaluation begins from the same locked task image used for baseline and golden validation.

The solver container and solver workspace are never reused.

## 5.7 Patch-order invariant

Candidate evaluation follows:

```text
Pristine baseline workspace
→ Candidate patch
→ Hidden test patch
→ Trusted preparation
→ Test execution
```

## 5.8 Protected-path invariant

If candidate and hidden patches modify any common path, the candidate is rejected by default:

```text
PATCH_CONFLICT
```

This prevents ambiguous evaluation behaviour.

## 5.9 Complete-result invariant

For every requested selector:

```text
exactly one normalised result must exist
```

The evaluator rejects:

* Missing selectors.
* Duplicate selector mappings.
* Unresolved selector mappings.
* Malformed structured output.
* Global collection failure.

## 5.10 Evidence invariant

Every lifecycle phase must preserve sufficient evidence to reconstruct:

* The source identity.
* The execution environment.
* The command invoked.
* The patch applied.
* The tests requested.
* The tests observed.
* The final classification.

---

# 6. Trust boundaries

## 6.1 Trusted components

For this assignment, the following are treated as trusted:

* CLI implementation.
* Task bundle metadata.
* Task-author Dockerfile.
* Pinned base environment image.
* Evaluation preparation script.
* Evaluation runner and parser.
* Hidden test patch.
* Golden patch.
* Task-author selector expectations.

A malicious Dockerfile or base image can execute code during build or runtime. The CLI does not claim to sandbox malicious task authors.

## 6.2 Untrusted components

The following are untrusted:

* Solver command.
* Solver context files.
* LLM-generated code.
* Candidate patch.
* Repository code executed during testing.
* Files created by the solver.
* Processes launched by candidate code.

## 6.3 Hidden-test threat model

The system guarantees:

* Hidden tests are absent from the solver image.
* Hidden tests are absent from the solver filesystem.
* Hidden selectors are absent from the solver prompt and environment.
* Candidate changes are finalised before hidden-test injection.
* Candidate evaluation occurs in a fresh evaluator.
* Evaluator harness and input files are root-owned and read-only.

The system does not claim that adversarial candidate code cannot inspect hidden test files while those tests are actively running in the same evaluation environment.

The precise claim is:

> Hidden tests are unavailable while the candidate solution is being generated. They are injected only after the candidate patch has been finalised and only inside a separate evaluator.

## 6.4 Host boundary

Untrusted containers must never receive:

* `/var/run/docker.sock`.
* The host home directory.
* SSH agent sockets.
* Cloud credentials.
* Git credentials.
* Arbitrary host environment variables.
* Privileged mode.
* Host PID namespace.
* Host network namespace.

---

# 7. Technology stack

| Concern           | Choice                | Reason                                              |
| ----------------- | --------------------- | --------------------------------------------------- |
| Language          | Python 3.12           | Assignment requirement and strong tooling support   |
| CLI               | Typer                 | Typed commands and clear help                       |
| Console           | Rich                  | Readable status and error output                    |
| Configuration     | YAML                  | Human-friendly task authoring                       |
| Validation        | Pydantic              | Strict schema and useful errors                     |
| Container control | Docker CLI            | Transparent commands and fewer compatibility issues |
| Persistence       | SQLite                | Lightweight local database                          |
| Migrations        | `PRAGMA user_version` | Sufficient for a small local schema                 |
| Testing           | pytest                | Standard Python test framework                      |
| Quality           | Ruff and mypy         | Formatting, linting and type checks                 |
| Hashing           | SHA-256               | Stable content identity                             |
| Packaging         | `pyproject.toml`      | Standard package format                             |

All subprocess execution uses structured argument lists and `shell=False`.

---

# 8. High-level architecture

```text
┌───────────────────────────────────────────────────────────┐
│                         task CLI                          │
│                                                           │
│ Bundle Loader   Lifecycle Orchestrator   Reporting Layer │
│      │                    │                     │          │
│      ▼                    ▼                     ▼          │
│ Schema/Lock        Docker Runtime       SQLite/Artefacts │
└────────────┬───────────────┬───────────────┬──────────────┘
             │               │               │
             ▼               ▼               ▼
     Baseline Evaluator  Solver Container  Candidate Evaluator
             │                                   │
             ▼                                   ▼
      Golden Evaluator                    Hidden evaluation
```

## 8.1 Core modules

### Bundle layer

* Loads YAML.
* Validates schema.
* Resolves paths.
* Computes digests.
* Reads and writes lockfiles.

### Source layer

* Fetches repositories.
* Verifies exact commits.
* Creates trusted worktrees.
* Produces sanitised repository snapshots.

### Runtime layer

* Builds images.
* Creates containers and volumes.
* Copies files.
* Executes commands.
* Captures logs.
* Enforces limits.
* Cleans resources.

### Lifecycle layer

* Implements `init`.
* Implements `validate`.
* Implements `run`.
* Implements `show`.
* Records events and statuses.

### Solver layer

* No-op solver.
* Patch-file solver.
* Command solver.

### Patch layer

* Extracts candidate changes.
* Parses changed paths.
* Applies policy.
* Detects conflicts.
* Verifies portability.

### Evaluation layer

* Stages evaluator files.
* Executes preparation.
* Runs task-specific tests.
* Validates structured results.
* Classifies outcomes.

### Persistence layer

* Commands.
* Events.
* Validations.
* Solver runs.
* Evaluations.
* Test results.
* Artefact references.

---

# 9. Task bundle structure

```text
my-task/
├── task.yaml
├── public/
│   ├── description.md
│   ├── requirements.md
│   └── interface.md
├── environment/
│   ├── Dockerfile
│   └── context/
├── evaluation/
│   ├── prepare.sh
│   ├── adapter.py
│   └── hidden/
│       ├── test.patch
│       └── golden.patch
└── README.md
```

Only paths explicitly referenced from `task.yaml` are considered task inputs.

The full bundle directory is never mounted into solver or evaluator containers.

---

# 10. Environment model

The CLI supports two explicit environment modes.

## 10.1 Custom Dockerfile

```yaml
environment:
  type: dockerfile
  dockerfile: environment/Dockerfile
  context: environment/context
```

The CLI creates a generated build context containing:

```text
Dockerfile
repo/
env/
```

The task-author Dockerfile must use this stable context contract.

## 10.2 Pinned base environment image

```yaml
environment:
  type: base_image
  image: ghcr.io/company/python-base@sha256:...
```

The configured image contains tools and dependencies, but is not the final task image.

The CLI generates a wrapper Dockerfile:

```dockerfile
FROM ghcr.io/company/python-base@sha256:...

COPY repo/ /opt/task/repo/

WORKDIR /opt/task/repo
```

The CLI then builds a task-specific image.

## 10.3 Unified invariant

In both modes:

> The CLI produces the final immutable task image containing the verified repository snapshot.

This avoids two different image semantics.

## 10.4 Automatic environment detection

Automatic detection is not part of the mandatory execution path.

A future helper may suggest configuration:

```bash
task inspect --suggest-config
```

The generated suggestion must be reviewed and written into the bundle. Execution always uses explicit locked configuration.

---

# 11. Proposed task schema

```yaml
schema_version: "1"

task:
  id: "openlibrary-worksearch-e010b2"
  title: "Fix worksearch document generation"

provenance:
  dataset: "ScaleAI/SWE-bench_Pro"
  dataset_revision: "<dataset-revision>"
  instance_id: "<full-instance-id>"
  source_record_sha256: "sha256:..."
  imported_at: "2026-07-28T12:00:00Z"

repository:
  url: "https://github.com/internetarchive/openlibrary.git"
  commit: "b70f9abab445676042e5c300dcf5dd8eac4afd18"
  submodules: false

public:
  description: "public/description.md"
  requirements: "public/requirements.md"
  interface: "public/interface.md"

environment:
  type: "dockerfile"
  dockerfile: "environment/Dockerfile"
  context: "environment/context"
  platform: "linux/amd64"

  build:
    timeout_seconds: 1800
    network: true
    no_cache: false
    build_args: {}

  runtime:
    working_directory: "/workspace/repo"
    user: "1000:1000"
    network: "none"
    timeout_seconds: 1800
    cpus: 2
    memory_mb: 4096
    pids_limit: 256
    read_only_root: true
    tmpfs:
      - "/tmp:size=512m"

evaluation:
  test_patch: "evaluation/hidden/test.patch"
  golden_patch: "evaluation/hidden/golden.patch"

  prepare:
    command:
      - "/evaluation/harness/prepare.sh"
    network: false

  runner:
    build_plan:
      - "python"
      - "/evaluation/harness/adapter.py"
      - "build-plan"
    parse_result:
      - "python"
      - "/evaluation/harness/adapter.py"
      - "parse-result"
    adapter_contract_version: "2"
    result_schema_version: "1"

  pass_to_pass:
    - selector: "openlibrary/plugins/worksearch/tests/test_worksearch.py::test_process_facet"

  fail_to_pass:
    - selector: "openlibrary/plugins/worksearch/tests/test_worksearch.py::test_get_doc"
      baseline_statuses:
        - failed

  repeat: 1

solver:
  timeout_seconds: 1800
  max_patch_bytes: 5242880
  max_changed_files: 200
  max_context_bytes: 10485760
  max_context_files: 500
  allow_network: false
```

---

# 12. Schema rules

## 12.1 Environment requirements

For:

```yaml
environment:
  type: dockerfile
```

the following are required:

* `dockerfile`
* `context`

For:

```yaml
environment:
  type: base_image
```

the following is required:

* Digest-pinned `image`

## 12.2 Public files

`description` is required.

`requirements` and `interface` are optional.

## 12.3 Baseline statuses

Default `FAIL_TO_PASS` baseline status:

```yaml
baseline_statuses:
  - failed
```

Tasks that intentionally produce a test-level runtime error may explicitly use:

```yaml
baseline_statuses:
  - error
```

A task may allow both:

```yaml
baseline_statuses:
  - failed
  - error
```

This must be an explicit task-author decision.

## 12.4 Path safety

Every referenced file must:

* Be relative to the bundle root.
* Resolve within the bundle.
* Exist.
* Not escape through symlinks.
* Match the expected type.
* Be included in the bundle digest.

Unknown YAML fields are rejected.

## 12.5 Secret policy

Build arguments must not contain secrets.

P0 policy:

* Public repositories only.
* No secret build arguments.
* No implicit host environment forwarding.
* Warn on build-argument names resembling `TOKEN`, `PASSWORD`, `SECRET` or credentials.
* Never claim that build arguments are confidential.

Future private dependency support should use BuildKit secret mounts.

---

# 13. Dataset provenance

Provenance is optional for manually authored tasks but required for imported benchmark tasks.

Stored fields:

* Dataset name.
* Dataset revision.
* Instance ID.
* Source record digest.
* Import timestamp.

This ensures the materialised bundle can be related to the exact upstream dataset record even if the dataset changes later.

The committed bundle remains the execution source of truth.

---

# 14. Bundle input digest

The system computes:

```text
bundle_input_digest =
  SHA256(
    canonical task configuration
    + public files
    + Dockerfile or base-image reference
    + environment context files
    + preparation script
    + runner and parser
    + hidden test patch
    + golden patch
    + selector definitions
    + repository URL
    + repository commit
    + provenance
    + target platform
    + non-secret build arguments
  )
```

Generated files are excluded:

```text
.task/
artifacts/
*.db
__pycache__/
temporary worktrees
generated build contexts
```

YAML is parsed and serialised into canonical JSON before hashing.

File contents are hashed independently and folded into the final digest.

---

# 15. Bundle lockfile

`task init` writes:

```text
.task/bundle.lock.json
```

Example:

```json
{
  "schema_version": "1",
  "task_id": "openlibrary-worksearch-e010b2",
  "bundle_input_digest": "sha256:...",
  "cli_version": "0.1.0",

  "provenance": {
    "dataset": "ScaleAI/SWE-bench_Pro",
    "dataset_revision": "...",
    "instance_id": "...",
    "source_record_sha256": "sha256:..."
  },

  "repository": {
    "url": "...",
    "requested_commit": "...",
    "resolved_commit": "...",
    "tree_sha": "..."
  },

  "environment": {
    "type": "dockerfile",
    "base_image": null,
    "dockerfile_sha256": "sha256:...",
    "task_image_id": "sha256:...",
    "task_image_reference": "task-bundle/openlibrary-worksearch:...",
    "platform": "linux/amd64",
    "runtime_policy_sha256": "sha256:..."
  },

  "evaluation": {
    "test_patch_sha256": "sha256:...",
    "golden_patch_sha256": "sha256:...",
    "harness_sha256": "sha256:...",
    "selectors_sha256": "sha256:..."
  },

  "created_at": "2026-07-28T12:00:00Z"
}
```

`validate` and `run` reject stale locks.

---

# 16. Repository acquisition

## 16.1 Host-side Git operations

The CLI fetches the repository into a controlled host temporary directory.

Representative flow:

```bash
git -c core.hooksPath=/dev/null clone --no-checkout <url> <directory>
git -C <directory> fetch --depth 1 origin <commit>
git -C <directory> checkout --detach <commit>
git -C <directory> rev-parse HEAD
git -C <directory> rev-parse HEAD^{tree}
```

## 16.2 Security controls

* Git hooks disabled.
* No credentials copied into build context.
* Submodules disabled by default.
* Remote metadata removed where practical.
* Exact commit verified.
* Temporary worktree removed after use.

## 16.3 Submodules

P0 supports:

```yaml
submodules: false
```

Tasks requiring submodules are deferred unless necessary for the selected real benchmark.

Candidate submodule modifications are rejected.

---

# 17. Safe generated build context

The full bundle is never passed to Docker build.

Generated context:

```text
generated-context/
├── Dockerfile
├── repo/
└── env/
```

Excluded:

```text
evaluation/
hidden patches
selectors
golden patch
SQLite database
artefacts
host configuration
credentials
solver context
```

This prevents hidden data leaking through:

* `COPY .`
* Image layers.
* Build cache.
* Image history.
* Build logs.

---

# 18. Task image layout

```text
/opt/task/repo/          pristine baseline repository
/opt/task/metadata/      non-sensitive locked metadata
/workspace/              runtime workspace location
/task/public/            solver-visible public context
/task/solver/            optional staged solver context
/evaluation/             evaluator-only staging location
```

The image must not contain:

* Hidden tests.
* Golden patch.
* Hidden selectors.
* Evaluation results.
* Solver credentials.
* Host credentials.

---

# 19. `task init`

## 19.1 Command

```bash
task init ./bundles/my-task
```

Options:

```bash
--rebuild
--no-cache
--platform linux/amd64
--keep-build-context
--json
```

## 19.2 Lifecycle

1. Create command ID.
2. Insert command record.
3. Validate bundle schema.
4. Validate referenced paths.
5. Enforce build-secret policy.
6. Calculate bundle input digest.
7. Fetch exact repository commit.
8. Verify commit and tree SHA.
9. Generate safe build context.
10. Generate wrapper Dockerfile when using a base image.
11. Build task image.
12. Inspect image metadata.
13. Reject declared volumes overlapping `/opt/task/repo`.
14. Export `/opt/task/repo` from a stopped immutable-image container.
15. Verify the complete normalized source manifest and raw Git tree SHA.
16. Run smoke check.
17. Write bundle lock.
18. Persist logs and environment metadata.
19. Clean temporary resources.

## 19.3 Smoke check

The CLI verifies:

* `/opt/task/repo` exists.
* Repository files are readable.
* Configured runtime user exists.
* Runtime working directory can be created.
* Optional smoke command succeeds.

The smoke probe is not the image-integrity boundary. Before smoke and lock
creation, the host independently validates every exported source path, type,
file digest/size/executable mode, and symlink target, then reconstructs the raw
Git tree. Add/delete/content/mode/type/target changes, `.git`, special entries,
unsafe paths, case collisions, and declared volume shadowing fail init.

## 19.4 Outcomes

```text
INITIALISED
CONFIG_ERROR
SOURCE_ERROR
BUILD_ERROR
IMAGE_ERROR
SMOKE_CHECK_ERROR
DATABASE_ERROR
```

---

# 20. Evaluator staging model

Evaluator files are injected after container creation.

## 20.1 Layout

```text
/evaluation/input/
├── plan.json
├── test.patch
├── golden.patch
└── task-metadata.json

/evaluation/harness/
├── prepare.sh
└── adapter.py

/evaluation/trusted/
└── executions.json
```

## 20.2 Permissions

```text
/evaluation/input
  owner: root
  files: 0444
  directories: 0555

/evaluation/harness
  owner: root
  scripts: 0555
  files: 0444

/evaluation/trusted
  owner: trusted administrator
  directories: 0555
  files: 0444
  staged only after candidate shutdown

/workspace/repo
  owner: evaluator user
  writable
```

## 20.3 Staging sequence

1. Create evaluator container.
2. Create evaluator-only storage.
3. Copy trusted input files.
4. Copy harness files.
5. Set root ownership and read-only permissions.
6. Seed baseline workspace.
7. Apply code patch administratively.
8. Apply hidden test patch administratively.
9. Build strict adapter execution plan version `2`.
10. Run preparation and structured execution units as the candidate UID.
11. Capture process streams/status/timeouts through Docker.
12. Stop the candidate container and prove `Running=false`, `Pid=0`, restart `no`.
13. Persist validated captured records and completed execution logs as
    command-owned host artifacts.
14. Stage captured records read-only.
15. Run a separate non-root trusted parser without the candidate workspace.
16. Validate bounded normalized parser stdout.
17. Destroy evaluator/parser containers and volumes.

Candidate-created structured result files are never accepted.

---

# 21. `task validate`

## 21.1 Command

```bash
task validate ./bundles/my-task
```

Options:

```bash
--repeat 3
--keep-containers
--json
```

## 21.2 Purpose

Validation proves:

* Baseline environment works.
* Baseline `PASS_TO_PASS` behaviour is correct.
* Baseline `FAIL_TO_PASS` behaviour matches explicit expectations.
* Golden patch applies.
* Golden patch makes all selected tests pass.
* Evaluation harness produces complete structured results.

## 21.3 Lifecycle

```text
Verify current lock
        │
        ▼
Fresh baseline evaluator
        │
Apply hidden test patch
        │
Run preparation
        │
Execute tests
        │
Validate baseline expectations
        │
        ▼
Destroy baseline evaluator
        │
        ▼
Fresh golden evaluator
        │
Apply golden patch
        │
Apply hidden test patch
        │
Run preparation
        │
Execute tests
        │
Validate all tests pass
        │
        ▼
Persist validation
```

## 21.4 Patch order

Baseline:

```text
Baseline → Hidden tests
```

Golden:

```text
Baseline → Golden patch → Hidden tests
```

## 21.5 Validation key

A successful validation is bound to:

* Bundle input digest.
* Task image ID.
* Runtime policy digest.
* Selector digest.
* Harness digest.

Changing any of these requires revalidation.

---

# 22. Test semantics

## 22.1 Harness statuses

```text
completed
collection_failed
crashed
timed_out
result_missing
parser_failed
prepare_failed
```

## 22.2 Individual statuses

```text
passed
failed
error
skipped
xfailed
xpassed
timeout
missing
```

## 22.3 `PASS_TO_PASS` baseline rule

Only:

```text
passed
```

is accepted.

## 22.4 `FAIL_TO_PASS` baseline rule

The actual status must appear in that selector’s explicit `baseline_statuses`.

Default:

```yaml
baseline_statuses:
  - failed
```

A test-level `error` is accepted only when explicitly configured.

Global infrastructure or collection errors are never accepted as intended baseline behaviour.

## 22.5 Golden rule

All selected tests must report:

```text
passed
```

## 22.6 Candidate rule

A candidate is resolved only when:

```text
All PASS_TO_PASS pass
AND
All FAIL_TO_PASS pass
AND
Harness completes
AND
Every selector is mapped exactly once
```

---

# 23. Evaluation plan

The CLI creates:

```json
{
  "schema_version": "1",
  "phase": "baseline",
  "repeat_index": 1,
  "pass_to_pass": [
    {
      "selector": "tests/test_api.py::test_existing"
    }
  ],
  "fail_to_pass": [
    {
      "selector": "tests/test_api.py::test_missing",
      "baseline_statuses": ["failed"]
    }
  ],
  "timeout_seconds": 1200
}
```

The runner may execute tests:

* Together.
* Separately by group.
* Individually.
* Through a repository-native harness.

The core does not dictate execution strategy.

The task-owned runner first emits adapter execution-plan schema version `2`:

```json
{
  "schema_version": "2",
  "executions": [
    {
      "execution_id": "pytest-group-001",
      "requested_selectors": [
        "tests/test_api.py::test_existing",
        "tests/test_api.py::test_missing"
      ],
      "argv": [
        "pytest",
        "-q",
        "tests/test_api.py::test_existing",
        "tests/test_api.py::test_missing"
      ],
      "timeout_seconds": 1200
    }
  ]
}
```

Schema version `1` is rejected with `ADAPTER_CONTRACT_UNSUPPORTED`. Execution
IDs are unique; selector lists and argv are non-empty; every requested selector
appears exactly once across all units; unknown selectors, unknown fields, shell
strings, duplicates, and excessive timeouts are rejected.

Docker then produces strict captured-record schema version `1` containing the
trusted execution ID/selectors/argv plus exit code, timeout, bounded stdout and
stderr, separate truncation flags, timezone-aware timestamps, duration, and
proven candidate termination. Captured records must correspond one-for-one
with the plan. They are atomically persisted as
`captured-executions.json` before being staged for the trusted parser. Parser
failure retains those records and completed execution logs but cannot create a
normalized result.

---

# 24. Normalised result contract

```json
{
  "schema_version": "1",
  "framework": "pytest",
  "harness_status": "completed",
  "collection_succeeded": true,
  "execution_started": true,
  "command": [
    "pytest",
    "<requested-selectors>"
  ],
  "started_at": "2026-07-28T12:00:00Z",
  "finished_at": "2026-07-28T12:00:03Z",
  "exit_code": 1,

  "tests": [
    {
      "requested_selector": "tests/test_api.py::test_create",
      "observed_id": "tests.test_api.test_create",
      "status": "failed",
      "duration_ms": 13,
      "message": "assert 400 == 201"
    }
  ]
}
```

## 24.1 Selector mapping

The task adapter maps framework-specific output to requested selectors.

The core enforces:

* One result per requested selector.
* No duplicate mappings.
* No unresolved selectors.

Framework adapters must define auxiliary-test handling explicitly. The committed
pytest adapters reject unexpected observed testcase IDs.

---

# 25. Repeat and flakiness

```yaml
evaluation:
  repeat: 1
```

For repeated validation, every selector must produce a stable expected result.

Example of unstable baseline:

```text
Run 1: failed
Run 2: passed
Run 3: failed
```

Outcome:

```text
INVALID_BASELINE_FLAKY
```

Repeat count may be overridden by CLI for diagnostic validation.

---

# 26. `task run`

## 26.1 No-op solver

```bash
task run ./bundle --solver noop
```

## 26.2 Patch solver

```bash
task run ./bundle \
  --solver patch \
  --patch ./candidate.diff
```

## 26.3 Command solver with installed executable

```bash
task run ./bundle \
  --solver command \
  -- python scripts/solve.py
```

The executable must already exist in the task image or repository.

## 26.4 Command solver with staged context

```bash
task run ./bundle \
  --solver command \
  --solver-context ./solvers/deterministic-stub \
  -- python /task/solver/solve.py
```

The CLI copies the validated solver context to:

```text
/task/solver/
```

## 26.5 Preconditions

Before solver execution:

* Lock must be current.
* Task image must match lock.
* Current validation must exist.
* Runtime policy must match validation.

## 26.6 Lifecycle

```text
Verify lock and validation
        │
        ▼
Fresh baseline preflight
        │
        ▼
Fresh solver container
        │
Stage public context
        │
Optionally stage solver context
        │
Execute solver
        │
Export solver workspace
        │
Destroy solver container
        │
Generate trusted candidate patch
        │
Validate patch policy
        │
        ▼
Fresh candidate evaluator
        │
Apply candidate patch
        │
Apply hidden tests
        │
Prepare and execute tests
        │
Classify candidate
        │
Persist report
```

---

# 27. Solver-context staging

## 27.1 Validation

The solver context:

* Must be outside the task bundle root.
* Must be outside artefact and database directories.
* Must not contain escaping symlinks.
* Must not contain sockets, devices or special files.
* Must respect size and file-count limits.
* Is hashed before execution.

## 27.2 Recorded metadata

```json
{
  "solver_context_sha256": "sha256:...",
  "solver_context_files": 14,
  "solver_context_bytes": 18342
}
```

## 27.3 Isolation

Solver context is:

* Not included in the task image.
* Not available to baseline or candidate evaluators.
* Mounted or copied read-only where practical.
* Separate from hidden task inputs.

---

# 28. Solver environment

```text
/opt/task/repo/       pristine baseline
/task/public/         public task content
/task/solver/         optional staged solver files
/workspace/repo/      writable solver workspace
/tmp/                 temporary storage
```

Environment variables may include:

```text
TASK_DESCRIPTION_FILE=/task/public/description.md
TASK_REQUIREMENTS_FILE=/task/public/requirements.md
TASK_INTERFACE_FILE=/task/public/interface.md
TASK_WORKSPACE=/workspace/repo
TASK_SOLVER_ROOT=/task/solver
```

No hidden information is passed.

---

# 29. Trusted candidate patch extraction

The solver’s Git metadata is never trusted.

## 29.1 Extraction algorithm

1. Solver modifies `/workspace/repo`.
2. Container is stopped.
3. Workspace is copied to a temporary host staging directory.
4. The exported filesystem is validated.
5. A fresh trusted Git checkout is created at the locked commit.
6. All worktree contents except trusted `.git` metadata are removed.
7. The validated solver filesystem is copied into the trusted worktree.
8. File permissions and executable bits are preserved.
9. `git add -A` stages modifications, additions and deletions.
10. Ignored untracked files remain excluded by default.
11. `git diff --cached --binary --full-index` generates the patch.
12. The patch is applied to a second fresh checkout.
13. The resulting tree is compared with the validated solver tree for supported files.
14. Temporary directories are removed.

## 29.2 Export validation

Reject:

* Device files.
* FIFOs.
* Sockets.
* Escaping symlinks.
* Excessive total size.
* Excessive file count.
* Path traversal.
* Unsupported submodule structures.

## 29.3 Deletions

Because the trusted worktree is cleared before copying the solver tree, baseline files missing from the exported solver workspace are represented as deletions.

## 29.4 Executable bits

Regular-file executable bits are preserved and included in the generated Git patch.

## 29.5 Ignored files

P0 behaviour:

* `git add -A` does not force-add ignored untracked files.
* Ignored untracked files are listed in solver diagnostics.
* Tasks requiring an intentionally ignored generated source file are outside the default P0 contract.

## 29.6 Extraction failure

If a valid portable patch cannot be produced:

```text
SOLVER_OUTPUT_ERROR
```

The mutable solver filesystem is never evaluated directly.

---

# 30. Candidate patch policy

## 30.1 Checks

* Patch parses.
* Patch applies to exact baseline.
* Maximum patch size.
* Maximum changed files.
* No absolute paths.
* No `..` traversal.
* No `.git` modification.
* No hidden-patch path overlap.
* No submodule change.
* No unsafe symlink.
* No unsupported special files.
* No malformed binary patch.

## 30.2 Conflict detection

```text
candidate changed paths ∩ hidden patch paths
```

If non-empty:

```text
PATCH_CONFLICT
```

## 30.3 Verification

Representative commands:

```bash
git apply --check candidate.patch
git apply --index --binary candidate.patch
git diff --cached --check
```

Patch verification occurs in a fresh trusted checkout.

---

# 31. Candidate evaluator

The evaluator:

1. Starts from locked task image.
2. Seeds pristine workspace.
3. Applies candidate patch administratively.
4. Applies hidden test patch administratively.
5. Stages root-owned evaluator files.
6. Runs optional preparation.
7. Drops to non-root evaluator user.
8. Executes tests.
9. Copies structured results and logs out.
10. Removes container and storage.

Candidate code cannot modify:

* Host files.
* Docker daemon.
* Previous workspaces.
* SQLite database.
* Host artefacts.

---

# 32. Runtime isolation

Representative restrictions:

```bash
docker create \
  --network none \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --read-only \
  --tmpfs /tmp:size=512m \
  --memory 4g \
  --cpus 2 \
  --pids-limit 256 \
  --user 1000:1000 \
  --mount type=volume,src=<workspace>,dst=/workspace \
  <task-image>
```

Mandatory controls:

* Non-root execution.
* Runtime network disabled.
* Capabilities dropped.
* `no-new-privileges`.
* Read-only root filesystem.
* Writable dedicated workspace.
* Temporary `/tmp`.
* Memory limit.
* CPU limit.
* PID limit.
* Wall-clock timeout.
* No privileged mode.
* No Docker socket.
* No arbitrary host mounts.
* No host credentials.

Rootless Docker is recommended and reported when detected, but not required.

---

# 33. Timeout and cleanup

On timeout:

1. Record timeout event.
2. Terminate active process.
3. Stop container.
4. Capture available logs.
5. Remove container.
6. Remove volume.
7. Persist final status.

Cleanup runs after:

* Success.
* Test failure.
* Solver failure.
* Patch failure.
* Timeout.
* Keyboard interruption.
* Parser failure.

Debug option:

```bash
--keep-containers
```

Use of this option is recorded in the command report.

---

# 34. Reproducibility model

## 34.1 Guaranteed identity

Every command records:

* Repository URL.
* Exact commit.
* Tree SHA.
* Bundle digest.
* Dataset provenance.
* Task image ID.
* Base image digest.
* Target platform.
* Runtime policy digest.
* Evaluation harness digest.
* Selector digest.
* Hidden patch digest.
* Golden patch digest.
* Candidate patch digest.
* Solver-context digest.
* CLI version.

## 34.2 Best-effort rebuild

Rebuilds can vary due to:

* Mutable package registries.
* Mutable OS repositories.
* Unpinned dependencies.
* Dynamic installation scripts.
* Architecture differences.

Recommended task-author practices:

* Digest-pinned base images.
* Dependency lockfiles.
* Frozen package installation.
* Vendored dependencies where practical.
* Explicit platform.
* Reuse of validated task image IDs.

## 34.3 Normalised runtime environment

Recommended defaults:

```text
TZ=UTC
LANG=C.UTF-8
LC_ALL=C.UTF-8
CI=true
PYTHONHASHSEED=0
```

Task-specific deterministic seeds may be added explicitly.

---

# 35. Persistence model

Default database:

```text
~/.task-bundle/task.db
```

SQLite settings:

```sql
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;
```

## 35.1 Commands

Stores:

* Command ID.
* Task ID.
* Command type.
* Command status.
* Outcome status.
* Timestamps.
* Bundle digest.
* Image ID.
* Exit code.

## 35.2 Command events

Stores lifecycle events such as:

```text
COMMAND_STARTED
BUNDLE_VALIDATED
SOURCE_FETCHED
IMAGE_BUILT
BASELINE_STARTED
BASELINE_COMPLETED
SOLVER_STARTED
SOLVER_COMPLETED
PATCH_EXTRACTED
CANDIDATE_COMPLETED
COMMAND_COMPLETED
CLEANUP_FAILED
```

## 35.3 Validations

Bound to:

* Bundle digest.
* Image ID.
* Runtime policy.
* Harness digest.
* Selector digest.

## 35.4 Solver runs

Stores:

* Solver type.
* Command argv.
* Context digest.
* Duration.
* Patch digest.
* Changed paths.
* Solver outcome.

## 35.5 Evaluations

Phases:

```text
baseline
golden
candidate
```

Stores:

* Harness status.
* Duration.
* Runner exit code.
* Patch digest.
* Outcome.

## 35.6 Test results

Stores:

* Group.
* Requested selector.
* Observed ID.
* Expected status.
* Actual status.
* Duration.
* Failure message.

## 35.7 Artefacts

Stores:

* Artefact type.
* Relative path.
* SHA-256.
* Size.

Large logs remain on disk rather than inside SQLite.

---

# 36. Command and outcome statuses

## 36.1 Command status

```text
running
succeeded
failed
interrupted
```

## 36.2 Validation outcome

```text
valid
invalid_baseline
invalid_baseline_flaky
invalid_golden
patch_error
infra_error
config_error
```

## 36.3 Solver outcome

```text
completed
failed
timed_out
output_error
```

## 36.4 Candidate outcome

```text
resolved
unresolved
guardrail_failure
solver_error
patch_error
patch_conflict
policy_violation
infra_error
```

An unresolved candidate may still have:

```text
command_status = succeeded
```

---

# 37. Error taxonomy

```text
CONFIG_ERROR
SOURCE_ERROR
BUILD_ERROR
IMAGE_ERROR
SMOKE_CHECK_ERROR
LOCK_MISMATCH
VALIDATION_REQUIRED
SOLVER_CONTEXT_ERROR
PATCH_EXTRACTION_ERROR
PATCH_APPLY_ERROR
PATCH_POLICY_ERROR
PATCH_CONFLICT
TEST_PREPARE_ERROR
TEST_RUNNER_ERROR
TEST_PARSE_ERROR
TEST_RESULT_INCOMPLETE
BASELINE_GUARDRAIL_ERROR
GOLDEN_VALIDATION_ERROR
SOLVER_ERROR
SOLVER_TIMEOUT
SOLVER_OUTPUT_ERROR
CONTAINER_ERROR
TIMEOUT
DATABASE_ERROR
CLEANUP_ERROR
```

Errors must identify:

* Failed phase.
* Expected behaviour.
* Actual behaviour.
* Relevant selector or path.
* Artefact location.
* Recommended corrective action.

---

# 38. Exit codes

|  Code | Meaning                                               |
| ----: | ----------------------------------------------------- |
|   `0` | Expected command goal succeeded                       |
|   `1` | Candidate evaluated correctly but remains unresolved  |
|   `2` | Usage, bundle or configuration error                  |
|   `3` | Build, container or evaluation infrastructure failure |
|   `4` | Invalid baseline or golden task                       |
|   `5` | Solver failure or timeout                             |
|   `6` | Patch extraction, policy or conflict error            |
| `130` | User interruption                                     |

---

# 39. Artefact structure

```text
artifacts/<task-id>/<command-id>/
```

## 39.1 Init

```text
command.json
bundle.snapshot.yaml
bundle.lock.json
environment.json
build-command.json
build.stdout.log
build.stderr.log
smoke.stdout.log
smoke.stderr.log
report.json
report.md
```

## 39.2 Validate

```text
baseline/
├── plan.json
├── test-results.json
├── stdout.log
├── stderr.log
├── prepare.stdout.log
├── prepare.stderr.log
└── patch-apply.log

golden/
├── plan.json
├── golden.patch
├── test-results.json
├── stdout.log
├── stderr.log
├── prepare.stdout.log
├── prepare.stderr.log
└── patch-apply.log

report.json
report.md
```

## 39.3 Run

```text
baseline/
├── plan.json
├── test-results.json
├── stdout.log
└── stderr.log

solver/
├── public-context.md
├── command.json
├── context-manifest.json
├── stdout.log
├── stderr.log
├── exported-tree-manifest.json
├── ignored-files.json
├── changed-files.json
└── candidate.patch

candidate/
├── plan.json
├── patch-apply.log
├── prepare.stdout.log
├── prepare.stderr.log
├── test-results.json
├── stdout.log
└── stderr.log

report.json
report.md
```

Full repository snapshots are not stored by default.

---

# 40. `task show`

```bash
task show <command-id>
```

Options:

```bash
--json
--events
--tests
```

Example:

```text
Command ID       cmd_9e19
Task             openlibrary-worksearch-e010b2
Command          run
Command status   SUCCEEDED
Evaluation       UNRESOLVED
Solver           noop
Duration         3m 42s

Baseline
  PASS_TO_PASS     1/1 passed
  FAIL_TO_PASS     1/1 matched expected baseline status

Candidate
  PASS_TO_PASS     1/1 passed
  FAIL_TO_PASS     0/1 passed

Artefacts
  artifacts/openlibrary-worksearch-e010b2/cmd_9e19/
```

---

# 41. Testing strategy

## 41.1 Unit tests

### Bundle

* Valid schema.
* Unknown fields.
* Conditional environment fields.
* Absolute paths.
* Path traversal.
* Symlink escape.
* Secret-like build arguments warning.
* Digest stability.
* Digest invalidation.
* Lock mismatch.
* Provenance serialisation.

### Source

* Exact commit checkout.
* Missing commit.
* Hooks disabled.
* Tree SHA recorded.
* Temporary cleanup.

### Solver context

* Valid context.
* Context inside bundle rejected.
* Escaping symlink rejected.
* Oversized context rejected.
* Special file rejected.
* Context digest stable.

### Patch extraction

* Modified file.
* Added file.
* Deleted file.
* Executable-bit change.
* Binary file.
* Ignored untracked file.
* Unsafe symlink.
* Special file.
* Oversized workspace.
* Fresh-checkout verification.

### Patch policy

* Valid patch.
* Path traversal.
* `.git` change.
* Hidden overlap.
* Submodule change.
* Oversized patch.
* Too many files.
* Malformed binary patch.

### Test semantics

* `PASS_TO_PASS` pass.
* `PASS_TO_PASS` fail.
* Default F2P failure accepted.
* F2P error rejected by default.
* Explicit F2P error accepted.
* Collection failure rejected.
* Missing selector.
* Duplicate mapping.
* Flaky result.
* Golden regression.
* Partial candidate.

### Persistence

* Command creation.
* Validation lookup.
* Solver metadata.
* Evaluation insertion.
* Test insertion.
* Artefact insertion.
* Transaction rollback.
* Migration idempotency.

## 41.2 Fake-runtime orchestration

* Init success.
* Build failure.
* Smoke failure.
* Baseline invalid.
* Golden invalid.
* Solver timeout.
* Solver-context failure.
* Patch extraction failure.
* Patch conflict.
* Candidate unresolved.
* Candidate resolved.
* Cleanup failure.

## 41.3 Synthetic Go task

Repository:

```text
calculator/
├── go.mod
├── calculator.go
└── calculator_test.go
```

Baseline:

```go
func Add(a, b int) int {
    return a - b
}
```

Tests:

```text
FAIL_TO_PASS
  TestAddPositive
  TestAddNegative

PASS_TO_PASS
  TestSubtract
```

Required demonstrations:

1. Init succeeds.
2. Validation succeeds.
3. No-op unresolved.
4. Golden patch resolves.
5. Partial patch unresolved.
6. Regression detected.
7. Command solver works with staged context.
8. Patch conflict rejected.
9. Hidden files inaccessible.
10. Network unavailable.
11. Non-root verified.
12. Timeout cleans resources.
13. Database lookup works.
14. JSON report validates.

## 41.4 Real SWE-bench Pro task

Use the supported Ansible instance. Its exact source tree has no Gitlinks.

Demonstrate:

```bash
task init bundles/swebench-pro-ansible-d9f186

task validate bundles/swebench-pro-ansible-d9f186

task run bundles/swebench-pro-ansible-d9f186 \
  --solver noop

task run bundles/swebench-pro-ansible-d9f186 \
  --solver patch \
  --patch /tmp/trusted-ansible-candidate.patch

task show <command-id>
```

Expected:

* Baseline valid.
* Golden valid.
* No-op unresolved.
* Golden candidate resolved.
* Results persisted and queryable.

The preserved OpenLibrary import is an unsupported-source boundary example.
Its verified tree contains Gitlinks and `task init` must fail with
`SOURCE_SUBMODULE_UNSUPPORTED`; validate/run are not expected to complete.

---

# 42. Security integration tests

Verify:

* Hidden files absent from solver.
* Golden patch absent.
* Hidden selectors absent.
* Bundle root absent.
* Docker socket absent.
* Host home absent.
* Network request fails.
* Solver UID is non-root.
* Capabilities are dropped.
* Resource limits are present.
* Timeout removes container.
* Candidate evaluator is fresh.
* Harness files are root-owned.
* Evaluation input is read-only.
* Candidate cannot create or replace accepted normalized output.
* Candidate shutdown is proven before non-root trusted parsing.
* Truncated, missing, duplicate, or unexpected pytest events fail closed.
* Containers are removed after success and failure.

---

# 43. Performance strategy

Correctness precedes optimisation.

P0:

* Reuse task image.
* Run selected tests only where possible.
* Use Docker build cache.
* Avoid full workspace snapshots.
* Stream and persist logs.
* Keep validation sequential.

Later:

* Git mirror cache.
* Content-addressed image cache.
* Parallel independent task runs.
* Optional repeated test optimisation.
* Remote execution.

---

# 44. Implementation phases

## Phase 0 — Foundation

* Package.
* CLI skeleton.
* Models.
* Errors.
* SQLite.
* Runtime protocol.
* Solver protocol.
* Test adapter protocol.
* Quality tooling.

### Gate

```text
task --help works
Ruff passes
mypy passes
unit tests pass
```

## Phase 1 — Init

* Bundle validation.
* Provenance.
* Digests.
* Git checkout.
* Safe context.
* Docker build.
* Base-image wrapper.
* Smoke check.
* Lockfile.

### Gate

Synthetic task initialises from a clean state.

## Phase 2 — Validate

* Evaluator staging.
* Permission model.
* Baseline evaluator.
* Golden evaluator.
* Structured runner.
* Explicit F2P statuses.
* Validation persistence.

### Gate

Synthetic baseline and golden invariants pass.

## Phase 3 — Run

* No-op solver.
* Patch solver.
* Command solver.
* Solver context.
* Host-side trusted extraction.
* Patch policy.
* Candidate evaluator.
* `task show`.

### Gate

No-op, golden, partial, regression and conflict scenarios behave correctly.

## Phase 4 — Security

* Non-root.
* Network isolation.
* Limits.
* Capability reduction.
* Timeouts.
* Cleanup.
* Security integration tests.

### Gate

All isolation claims are proven by tests.

## Phase 5 — Real benchmark

* Supported Ansible bundle.
* Preserved OpenLibrary gitlink rejection evidence.
* Provenance.
* Docker environment.
* Evaluation runner.
* Real validation.
* Real run reports.

### Gate

Clean-machine Ansible workflow succeeds; OpenLibrary remains correctly blocked.

## Phase 6 — Submission polish

* CI.
* Documentation split.
* Troubleshooting.
* Example artefacts.
* Private GitHub repository.
* Share with required reviewers.

---

# 45. Documentation structure

The master specification should be split for submission.

## README.md

Operational entry point:

1. What the tool does.
2. Architecture diagram.
3. Installation.
4. Bundle structure.
5. Four primary commands.
6. Synthetic example.
7. SWE-bench Pro example.
8. Security limitations.
9. Links to design documents.

## DESIGN.md

Reviewer-focused design:

1. Goals and non-goals.
2. Correctness invariants.
3. Lifecycle.
4. Trust boundaries.
5. Environment and evaluation contracts.
6. Reproducibility.
7. Persistence and artefacts.
8. Main trade-offs.

## docs/DEEP-DIVE.md

Detailed implementation specification:

* Full schema.
* Permission model.
* Patch extraction.
* SQL schema.
* Error taxonomy.
* Artefact trees.
* Test matrices.
* Implementation phases.
* Acceptance checklist.

This document is the source specification from which those reviewer-facing files should be derived.

---

# 46. Key trade-offs

| Decision                     | Rejected alternative             | Reason                                                |
| ---------------------------- | -------------------------------- | ----------------------------------------------------- |
| CLI-produced task image      | Opaque final prebuilt task image | Preserves one consistent source invariant             |
| Explicit environment         | Universal auto-detection         | More reliable for arbitrary repositories              |
| Separate solver/evaluator    | Single mutable container         | Prevents pre-solution leakage and dirty evaluation    |
| Host-side trusted extraction | Solver-controlled Git metadata   | Easier to prove and secure                            |
| Explicit F2P statuses        | Automatically accept all errors  | Prevents unrelated failures being treated as intended |
| Structured JSON              | Console parsing                  | Reliable per-test semantics                           |
| Task-specific runner         | Framework logic in CLI core      | Preserves language independence                       |
| Command solver context       | Undefined external script path   | Makes solver delivery explicit                        |
| Read-only evaluator inputs   | Writable shared evaluator tree   | Reduces ordinary harness mutation                     |
| SQLite                       | PostgreSQL                       | Meets requirements without extra infrastructure       |
| Docker                       | MicroVM                          | Best portability/isolation trade-off for take-home    |
| Deterministic solvers first  | Rushed LLM integration           | Prioritises infrastructure correctness                |
| Commit plus patch            | Full repository archives         | Smaller and usually sufficient                        |
| Sequential validation        | Early parallelism                | Simpler logs, cleanup and debugging                   |

---

# 47. Final acceptance checklist

## Correctness

* [ ] Exact repository commit used.
* [ ] CLI-produced task image used.
* [ ] Baseline P2P passes.
* [ ] Baseline F2P matches explicit status policy.
* [ ] Harness failures are rejected.
* [ ] Golden patch passes all tests.
* [ ] Candidate evaluator is fresh.
* [ ] Partial success is reported.
* [ ] Regressions are reported.
* [ ] Missing selectors invalidate evaluation.
* [ ] Candidate resolution requires all tests to pass.

## Hidden-test protection

* [ ] Hidden patch absent from build context.
* [ ] Hidden patch absent from task image.
* [ ] Hidden patch absent from solver filesystem.
* [ ] Hidden selectors absent from solver context.
* [ ] Golden patch absent from solver.
* [ ] Candidate finalised before hidden injection.

## Solver delivery and extraction

* [ ] Command solver supports installed executable.
* [ ] Command solver supports staged context.
* [ ] Solver context digest recorded.
* [ ] Solver Git metadata is not trusted.
* [ ] Host-side extraction preserves modifications and deletions.
* [ ] Generated patch verified in fresh checkout.
* [ ] Unsafe exported files rejected.

## Evaluator integrity

* [ ] Input root-owned and read-only.
* [ ] Harness root-owned and read-only.
* [ ] Output separated and writable.
* [ ] Candidate patch applied administratively.
* [ ] Hidden patch applied administratively.
* [ ] Tests run as non-root.

## Isolation

* [ ] Runtime network disabled.
* [ ] Docker socket absent.
* [ ] Host mounts absent.
* [ ] Credentials absent.
* [ ] Capabilities dropped.
* [ ] `no-new-privileges`.
* [ ] CPU limit.
* [ ] Memory limit.
* [ ] PID limit.
* [ ] Timeout.
* [ ] Cleanup.

## Reproducibility

* [ ] Provenance recorded.
* [ ] Commit recorded.
* [ ] Tree SHA recorded.
* [ ] Bundle digest recorded.
* [ ] Base image digest recorded.
* [ ] Task image ID recorded.
* [ ] Runtime policy recorded.
* [ ] Harness digest recorded.
* [ ] Patch digests recorded.
* [ ] Solver-context digest recorded.
* [ ] Stale lock detected.

## Observability

* [ ] Stable command ID.
* [ ] Lifecycle events.
* [ ] stdout and stderr.
* [ ] Patch logs.
* [ ] Candidate patch.
* [ ] Test-level results.
* [ ] JSON report.
* [ ] Markdown report.
* [ ] Command query.

## Demonstration

* [ ] Synthetic Go task validates.
* [ ] Synthetic no-op unresolved.
* [ ] Synthetic golden patch resolves.
* [ ] Command solver context works.
* [ ] Real SWE-bench Pro task initialises.
* [ ] Real task validates.
* [ ] Real no-op report generated.
* [ ] Real golden report generated.
* [ ] Database lookup demonstrated.

---

# 48. Final recommendation

Proceed with the following fixed decisions:

1. Python 3.12, Typer, Pydantic, Rich and SQLite.
2. Docker CLI through a narrow runtime abstraction.
3. CLI-produced task images in all environment modes.
4. Explicit Dockerfile or digest-pinned base environment image.
5. Safe generated build contexts.
6. Dataset provenance for imported benchmark tasks.
7. Complete bundle input digest and immutable lockfile.
8. Separate baseline, golden, solver and candidate containers.
9. Explicit `FAIL_TO_PASS` baseline status policies.
10. Root-owned read-only evaluator inputs and harness.
11. No-op, patch and command solvers.
12. Explicit command-solver context staging.
13. Host-side trusted candidate patch extraction.
14. Fresh-checkout candidate patch verification.
15. Strict patch conflict and policy handling.
16. Runtime network isolation and resource controls.
17. SQLite lifecycle, evaluation and test records.
18. Complete JSON, Markdown and raw execution artefacts.
19. Synthetic Go validation before the real benchmark.
20. Documentation split into README, DESIGN and DEEP-DIVE.
21. No optional feature work before the complete mandatory flow passes.

The architecture is now complete at both conceptual and implementation-contract levels.

The primary remaining risk is execution quality:

* Whether task image construction works consistently.
* Whether the solver context is staged safely.
* Whether workspace export and patch generation preserve intended changes.
* Whether evaluator permissions behave correctly across platforms.
* Whether the real SWE-bench Pro task runs from a clean machine.

Those risks are now explicit, testable and contained within the implementation plan.

This design represents a strong production-oriented foundation without sacrificing the implementation focus required for a take-home assignment.
