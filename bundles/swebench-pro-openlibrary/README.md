# SWE-bench Pro: OpenLibrary Project Runeberg

This is a preserved unsupported-source example, not a runnable Task Bundle
lifecycle. Provenance and selector semantics remain verified, but the exact
source tree contains Gitlinks at `vendor/infogami` and `vendor/js/wmd`;
`task init` correctly fails with `SOURCE_SUBMODULE_UNSUPPORTED`.

This bundle imports one immutable record from `ScaleAI/SWE-bench_Pro` at
revision `7ab5114912baf22bb098818e604c02fe7ad2c11f`.

The complete source row was serialized as sorted, compact UTF-8 JSON with no
trailing newline. Its SHA-256 is
`sha256:c48f1cee513d00ebe7093c3e6c08590d971f1827b72a4e7cce7140acad396d3a`.
The upstream row is identified by the `instance_id` in `task.yaml`; the
canonical record URL and import procedure are recorded in
`provenance/README.md`.

Manual transformation was deliberately mechanical:

- `problem_statement` became `public/description.md` without rewriting.
- `requirements` became `public/requirements.md` without rewriting.
- `interface` became `public/interface.md` without rewriting.
- `test_patch` and `patch` became the hidden test and golden patch.
- the stringified selector lists became typed selector entries in `task.yaml`.
- the upstream base commit became the exact public repository identity.

Generated `.task/`, `artifacts/`, and database files are intentionally absent.

## Known source-policy blocker

The exact base commit contains Gitlinks at `vendor/infogami` and
`vendor/js/wmd`. Task Bundle CLI deliberately rejects Gitlinks and does not
initialize submodules. Consequently, the committed bundle is a faithful import
and its official image/runner can verify the selectors, but `task init` stops
with `SOURCE_SUBMODULE_UNSUPPORTED` before image construction. This is not
worked around by flattening or deleting entries because doing so would break
the locked raw-tree identity. Supporting submodules is explicitly outside the
Phase 6 product scope.
