# Runnable submission example

This deliberately small public task exercises the same init, validation,
solver, candidate-tree, round-trip, policy, and hidden-evaluation lifecycle as
the real benchmark. It is suitable for a reviewer smoke run before the larger
OpenLibrary demonstration.

The repository commit and Python base are immutable. Runtime evaluation has no
network. `candidates/golden.patch` is a reviewer-owned external patch input,
not a solver-visible bundle mount.
