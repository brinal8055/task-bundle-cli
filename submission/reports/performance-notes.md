# Performance notes

On Docker Desktop with `linux/amd64` emulation:

- first example init, including public fetch and image build: about 34 seconds
- baseline plus golden validation: 3.44 seconds evaluator time
- no-op full run: 14.0 seconds
- patch full run: 14.3 seconds
- recursive isolation command full run: 14.5 seconds

The solver stop/export sequence accounts for roughly ten seconds because the
current implementation requests a graceful Docker stop before export.
Subsequent init can use Docker's normal build cache; no application cache was
added. Runs remain sequential and retain only bounded logs/manifests/patches,
not complete workspace snapshots.

The official OpenLibrary image is substantially larger and runs under
architecture emulation on this host. Direct selected-test execution after image
pull took well under a second of pytest time; image transfer/extraction was the
dominant first-run cost.

The repository's complete Go lifecycle (two validation repeats and eight runs)
took 736.32 seconds under `linux/amd64` emulation. This includes no-op, golden,
partial, P2P regression, deterministic command, hidden-isolation command,
hidden conflict, malformed patch, all `task show` service queries, and cleanup.
