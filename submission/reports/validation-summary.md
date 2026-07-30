# Real-Docker validation summary

Environment: Docker Desktop 4.75, Engine 29.5.2, Apple Silicon host using
`linux/amd64` emulation.

Runnable example:

- init command: `cmd_dbf9acc81ba148be8df650c5be68e3f3`
- validation command: `cmd_4f3ba029afa54ce3affac59285699e90`
- validation identity: `val_91dfc450ae85727f6f0dc1293a5c1495`
- bundle digest:
  `sha256:c66c1453ea364cc651f276d2103bca580de18df97ac45a0bd2f7b109ac792d16`
- source tree digest:
  `sha256:da522eef1c66b0e42c2a191577bf727625061e001c6048c1bf1ea4b6840cd724`
- image ID:
  `sha256:4c4a3427ec1bb269e57c7bbab7c38af6c8b5cb3be7889ff22476c53f433e75b8`
- baseline: P2P 1/1, F2P 1/1, accepted, 1,608 ms
- golden: P2P 1/1, F2P 1/1, accepted, 1,818 ms
- cleanup: complete, no retained containers

Direct runtime security probe under the same restrictions:

```text
uid=1000 gid=1000 groups=1000
CapEff: 0000000000000000
NoNewPrivs: 1
Docker socket: absent
Outbound TCP attempt: failed
```

OpenLibrary official-image selector check:

- base commit: `b70f9abab445676042e5c300dcf5dd8eac4afd18`
- image digest:
  `sha256:bc8926c47ffdb38edd165d80c776044a277cf5fd0c6fab80420d928917d4a65e`
- exact hidden patch: `test_process_facet` passed and `test_get_doc` failed
- exact hidden plus golden patches: both selectors passed

OpenLibrary Task Bundle validation was not run because init correctly stopped
at the two source Gitlinks. See `openlibrary-blocker.md`.

Selected supported Ansible task:

- init command: `cmd_d326a7f751fe4f30b908509ef66ab691`
- validation command: `cmd_ce2d13ef592844bb8e3bb840165f4dd0`
- validation identity: `val_8834fc8c0a1d5fd85c0af82e11299f8c`
- bundle digest:
  `sha256:8ff827c8fcbeb6a4f3d41bca36140401b2d486d36be1bd1212c0a015d26ac1c2`
- source tree SHA: `64a85753dada2a0a05dcf13093dabbdae13cc7de`
- image ID:
  `sha256:eb28ca0a71e1b09885622ab752d23126c797f1bfb5d75d09540b03027fa93b85`
- harness digest:
  `sha256:c91055236e7fa06a17204455cd1082a5d4b6dd873fe56e45d3606f6a1bbe70df`
- baseline: P2P 1/1 passed; F2P 1/1 failed as configured
- golden: P2P 1/1 passed; F2P 1/1 passed
- adapter contract: version 2, grouped pytest execution, strict observed IDs
- parser: dedicated non-root `65532:65532`, candidate shutdown proven first
- cleanup: complete, no retained evaluator containers or volumes
