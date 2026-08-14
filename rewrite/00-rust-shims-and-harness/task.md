# Rust provider shims and test harness

Status: completed. The coordinator may call this the bootstrap step, but
implementors and reviewers receive only this scoped task bundle.

## Objective

Write Rust shims for Claude Code, Codex, and OpenCode. Install new shims
globally while leaving legacy shims installed and functional simultaneously.
The shims are thin pass-through/evidence edges, never a second daemon or
semantic decision-maker.

## Required behavior

- Detect the real provider executable without recursion; preserve argv, stdin,
  stdout, stderr, exit status, signals, cwd, environment, and timing.
- Capture only registered evidence. Unknown observational input becomes generic
  evidence; answerable/delegating unsupported behavior fails closed.
- Support Claude, Codex, and OpenCode independently; nested invocation is safe.
- Install/uninstall/upgrade atomically with owner-only permissions, hashes,
  provenance, health verification, and rollback. Old/new shims coexist under
  distinct executable names or explicit dispatch configuration.
- Never spool, replay, queue mutations, or silently retry provider actions.
  Daemon outage means pass-through plus an ingestion gap.
- Provide deterministic test-fixture mode only in tests; production behavior
  must remain unchanged.

## Quality and tests

Use the pinned stable Rust toolchain selected by rustup (`rustup show active-
toolchain` must report the configured toolchain and the host target must be
available), `rustfmt --check`,
`cargo clippy --all-targets --all-features -- -D warnings`, dependency audit,
unit tests, process integration tests, install/rollback tests, and separate
end-to-end tests for each provider. Prove old/new global installation
coexistence and unchanged execution of each real provider.

## Handoff

Publish executable paths, install manifest, rollback instructions, hashes,
fixtures, unit/e2e reports, and manual-provider smoke report to the root index
evidence fields. Python quality gates begin with the next task and apply to all
Python code thereafter.
