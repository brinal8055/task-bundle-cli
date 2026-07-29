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
- exact-once task-owned selector mapping into the normalized schema;
- automatic container and volume cleanup.

The official benchmark image is used only as a digest-pinned dependency base.
Its inherited interactive entrypoint is reset, and the task runner explicitly
imports Ansible from `/workspace/repo/lib`, so patched candidate source—not
the image's `/app` checkout—is tested.

OpenLibrary is intentionally still rejected for unsupported Gitlinks. No
source validation or isolation property was weakened to close the submission.
