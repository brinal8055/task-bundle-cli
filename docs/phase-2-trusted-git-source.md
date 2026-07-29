# Phase 2: Trusted Git Source Materialisation

Phase 2 turns a public HTTPS repository URL and exact 40-character commit SHA
into verified, temporary source content. It performs no Docker, solver, or test
execution.

## Source policy

Repository URLs must use HTTPS and contain a host and repository path. Local,
SSH, Git-protocol, remote-helper, credential-bearing, query, and fragment URLs
are rejected. Commits must be complete hexadecimal SHA-1 object IDs; branches,
tags, abbreviations, and revision expressions are not accepted as input.

Git runs with an explicit minimal environment. Interactive prompting and
askpass are disabled, global and system configuration are ignored, credentials
and host secrets are not forwarded, hooks are disabled, submodule recursion is
off, and all protocols except HTTPS are denied.

## Verification and materialisation

The service creates a temporary bare object repository and fetches only the
requested commit with `--no-tags --depth=1`. It verifies that the requested
object is itself a commit, confirms the resolved commit matches exactly,
records the tree SHA, and rejects mode `160000` gitlinks regardless of whether
`.gitmodules` exists.

The verified commit is exported with `git archive`. Archive members are
validated before manual extraction: absolute and traversing paths, hard links,
devices, FIFOs, special entries, `.git`, and entries nested beneath symlinks are
rejected. Relative symlinks are supported only when their lexical target stays
within the source root. Symlinks are created last and are never dereferenced by
the extractor or manifest builder.

## Identity and persistence

The source manifest records sorted repository-relative files using canonical
`0644`/`0755` modes, sizes, and SHA-256 content hashes. Safe symlinks record
their exact target. Its canonical JSON digest is independent of temporary
paths, ownership, and timestamps.

`ResolvedSource` records the validated URL, requested and resolved commits,
tree SHA, source digest and counts, Git executable and version, and a UTC
creation timestamp. Metadata can be atomically persisted at:

- `.task/source.snapshot.json`
- `.task/source.manifest.json`

The materialisation API is a context manager. Its bare repository, isolated
HOME/config, archive, and extracted source tree are removed after success,
exceptions, and interruptions.

## Limitations

- Public HTTPS repositories only
- No credentials or private repositories
- No submodules
- No Git cache or fallback fetch widening
- Backslashes in repository paths and symlink targets are rejected for
  cross-platform path safety
- No Docker, task image, or final bundle lock
