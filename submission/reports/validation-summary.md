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

- init command: `cmd_ead91f81c1534e468029b0c977327422`
- validation command: `cmd_95994ac450a94272bf1d163c09ede125`
- validation identity: `val_44a4f3d3180ec8101c04d572ef9d8e10`
- bundle digest:
  `sha256:5e70d5e6c559d9c2267ec13b2d8ba04b6944012792dda3e5b58c8d8fd2a426bd`
- source tree SHA: `64a85753dada2a0a05dcf13093dabbdae13cc7de`
- image ID:
  `sha256:1588d122d2722c2586e41b7abc2c4c0f4cb7e046ddb90a7c886f1c1ddf2bba08`
- baseline: P2P 1/1 passed; F2P 1/1 failed as configured
- golden: P2P 1/1 passed; F2P 1/1 passed
- cleanup: complete, no retained evaluator containers
