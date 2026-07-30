# Submission security summary

The successful Ansible demonstration preserves the same security boundary as
the synthetic lifecycle:

- exact public HTTPS commit and raw Git tree;
- zero Gitlinks and no submodules;
- digest-covered bundle, source, image, runtime, harness, selectors, and
  patches;
- non-root solver and evaluator commands;
- no runtime network, Docker socket, effective capabilities, or privilege
  escalation;
- read-only container root plus bounded writable volumes;
- hidden patch, golden patch, selectors, bundle root, database, and artifact
  root absent from the solver;
- bounded workspace export with safe regular files and relative symlinks;
- regenerated binary patch, complete manifest round-trip, policy, and hidden
  conflict checks before fresh evaluator creation;
- adapter contract version `2` with task-owned grouped execution plans;
- Docker-captured argv, stdout/stderr, exit, timeout, and truncation state;
- proven candidate shutdown before a separate non-root trusted parser;
- no candidate-writable accepted normalized-result path;
- exact-once requested/observed selector mapping with fail-closed truncation;
- full image `/opt/task/repo` export, normalized manifest, and raw-tree equality;
- rejection of Docker volumes overlapping the protected image source path;
- automatic container and volume cleanup.

The official benchmark image is used only as a digest-pinned dependency base.
Its inherited interactive entrypoint is reset, and the task runner explicitly
imports Ansible from `/workspace/repo/lib`, so patched candidate source—not
the image's `/app` checkout—is tested.

OpenLibrary is intentionally still rejected for unsupported Gitlinks. No
source validation or isolation property was weakened to close the submission.

Candidate code executing inside pytest can still interfere with pytest itself.
The remediation prevents direct final-result spoofing and post-test overwrite
races; it does not claim a cryptographically isolated in-process oracle.
