# Rewrite implementation plan

This is the progress index. Each step has one `task.md`; there are no
per-step index files. Update this file whenever a step changes state.

The complete section-to-step and generated-contract ownership map is
[spec-coverage.md](spec-coverage.md).

All blockers are recorded in [blockers.md](blockers.md). This plan is fully
autonomous: agents do not ask the user questions while executing. They record
uncertainty and continue independent work; only the dependent chain is paused.

Status values: `pending`, `in_progress`, `blocked`, `completed`.

| Step | Task | Status | Depends on | Evidence |
|---|---|---|---|---|
| 00 | [Rust provider shims and test harness](00-rust-shims-and-harness/task.md) | completed | — | code: baqylau2@c44b561fdae4938914cb2be1d966ccdbb29c53bd; install: ~/.baqylau (manifest schema v1, 6 shims + statusline shim + TS plugin, digests recorded, config .bak preserved and verified pristine); tests: 185 Rust + 19 TS = 204 passed, 0 failed (unit/fixtures/install-rollback/hook/e2e/process-integration), fmt clean, clippy -D warnings clean, cargo audit 0 findings, tsc clean; rollback: `baqylau-edge-install rollback` proven byte-exact against pristine pre-install snapshot; reviews: 2 independent passes, both approved; manual tests: Claude Code/Codex/OpenCode all pass, coexistence with legacy shims confirmed live; blocker: BLOCKER-STEP00-001 (design has no OpenCode plugin contract; implemented against OpenCode's real runtime contract instead, non-blocking) |
| 01 | [Inventory and fixtures](01-inventory-and-fixtures/task.md) | completed | 00 | code: baqylau2@5e9c3aa (phase0/); manifest: 113 endpoints set-equal across §38.24/§38.38, 36 events, 5 SQL units executing clean (quick_check ok, foreign_key_check 0 rows, 152 tables/127 triggers/93 indexes), ownership 2070 rows 0 unowned, test matrix 486 rows covering §30/§38.31/§40.7/§41/§42/§43; fixtures: 20 fixtures/58 artifacts under phase0/fixtures with authenticity declared (captured/derived/synthetic), operator PII and third-party confidential content purged from corpus and git history (verified via full git object-database scan, 0 hits); parity: 38 oracles (27 captured) at legacy commit 8c67811, 2 honestly withheld (no clean third-party-free source exists); decisions: 26 executable checks + 16 fail-closed mutation tests, all passing; tests: ./phase0/run_all.sh --verify green; rollback: n/a (research/cataloguing step, no production code); reviews: 2 independent passes, both approved (review 2 confirmed both its blockers fixed before hitting a session limit); manual tests: usability pass + 2 confidentiality re-tests, all pass; blockers: BLOCKER-STEP01-001 (undeclared final schema digest), BLOCKER-STEP01-002 (no OpenCode edge manifest in design), BLOCKER-STEP01-003 (5 unlisted Claude hook families with real traffic), BLOCKER-STEP01-004 (legacy reader/audit discrepancy for Step 10) |
| 02 | [Foundation and storage](02-foundation-and-storage/task.md) | pending | 01 | code: —; migration: —; manifest: —; tests: —; rollback: — |
| 03 | [First vertical slice](03-first-vertical-slice/task.md) | pending | 02 | code: —; migration: —; manifest: —; tests: —; rollback: — |
| 04 | [Canonical read model](04-canonical-read-model/task.md) | pending | 03 | code: —; migration: —; manifest: —; tests: —; rollback: — |
| 05 | [Streaming and rendering](05-streaming-and-rendering/task.md) | pending | 04 | code: —; migration: —; manifest: —; tests: —; rollback: — |
| 06 | [Projections and machine services](06-projections-and-machine-services/task.md) | pending | 05 | code: —; migration: —; manifest: —; tests: —; rollback: — |
| 07 | [Controls and effects](07-controls-and-effects/task.md) | pending | 06 | code: —; migration: —; manifest: —; tests: —; rollback: — |
| 08 | [Providers, backends, and accounts](08-providers-backends-and-accounts/task.md) | pending | 07 | code: —; migration: —; manifest: —; tests: —; rollback: — |
| 09 | [Handover, collaboration, and future features](09-handover-collaboration-and-future-features/task.md) | pending | 08 | code: —; migration: —; manifest: —; tests: —; rollback: — |
| 10 | [Migration, parity, and cutover](10-migration-parity-and-cutover/task.md) | pending | 09 | code: —; migration: —; manifest: —; tests: —; rollback: — |

Every task must also produce or consume the machine-checkable manifests below
from the design; these are implementation context, not optional summaries:

- endpoint manifest: every method/path, operation ID, auth scope, request,
  response, error, storage owner, event, idempotency, and revision rule from
  §§38.24, 38.36, 38.38;
- event manifest: every durable SSE event, feed, live frame, producer
  transaction, reducer, authorization revision, cursor, replay/resnapshot,
  and payload from §§38.22, 38.36, 38.38;
- schema manifest: every table, column, constraint, index, trigger, migration
  unit, digest, retention class, deletion law, port, query key, and transaction
  from §§38.25–38.27, 38.34–38.35, 38.39, 40.7;
- ownership manifest: every service, storage protocol, worker, provider role,
  terminal role, DTO, fixture, and performance/security/recovery gate has one
  owner and one completion step.

Each status update must link the completed code, migration, generated artifact,
fixture/gate output, and rollback evidence. “Completed” means the assigned
manifest rows are implemented and verified, not merely read.

## Global rules

- `docs/rewrite-design-v4-codex.md` is the complete specification. Every task
  must read the entire document before coding and implement all requirements
  in its assigned sections and their references: DDL, OpenAPI, events, DTOs,
  ports, adapters, fixtures, performance, security, retention, recovery, and
  failure alternatives. Task files distribute work; they do not remove
  requirements.
- Binding decisions: one machine-wide SQLite database; daemon accepted as the
  single point of failure; no client/edge/remote spool, replay, or offline
  queue; full future-feature scope; retained durability/correlation/audit/
  retention complexity; hexagonal dependency direction; provider knowledge
  confined to adapters.
- Preserve `unknown`/`zero`/`unsupported`, requested/effective state,
  silence-never-proves-success, durable ownership of load-bearing facts,
  source-order rather than wall-clock order, and evidence for every skip,
  failure, timeout, and reconciliation.
- Do not hide implementation gaps by editing the design. Record any new
  contradiction as a blocking decision in this index and the task notes.
- Never ask the user for clarification during autonomous execution. Add a
  complete entry to `rewrite/blockers.md` instead.
- A step is complete only when code, migrations, API/event contracts,
  provider/terminal adapters, tests, fixtures, performance/security checks,
  recovery, retention, and rollback are complete.
- Every implementation step writes unit tests and end-to-end tests before
  sign-off. Python must pass Pylint with all rules enabled and strict static
  typing with no new errors. Python formatting is Black (`black --check` after
  the implementor runs Black); Ruff is an additional lint/import check, not
  the formatter. Rust formatting is `rustfmt --check` and Rust linting is
  `cargo clippy --all-targets --all-features -- -D warnings`.
- Every step receives a fresh independent agent set: one new implementor, one
  new first reviewer, one new second reviewer, and one new manual tester. The
  same implementor remains responsible for fixes within that step, but no
  agent is reused for another step. The orchestrator addresses role
  capabilities, not model names or effort levels.
- Each step receives two independent code-review passes. The implementor fixes
  findings after each pass and reruns all checks. A manual tester exercises
  Claude Code, Codex, and OpenCode one by one; failure repeats the same-step
  implementor/reviewer/manual loop.
- Implementors and reviewers receive only their scoped task bundle and
  required artifacts; they are not told the step number or phase name.
- New implementation code lives in `/Users/z.yermagambet/code/personal/baqylau2`.
  This repository remains the legacy reference and fixture source.

## Ownership boundaries

- Step 04 owns read DTO/query composition for tasks, memory, stats, scoreboard,
  resources, drafts, overviews; Step 06 owns their fact ingestion/projection
  writers and workers.
- Step 05 owns stream/render/live-frame presentation; Step 06 owns attention,
  presence, compaction, and view-mode truth/projection. Step 05 consumes them.
- Step 06 owns notification truth/arms; Step 07 owns notification delivery
  effects and control drivers. Step 06 owns account/quota facts; Step 07 owns
  account migration effect execution; Step 08 owns provider-specific launch,
  credentials, and account adapters.
- Step 08 owns remote backend/provider protocol adapters; Step 09 owns only
  future-feature contract rows and plugin/relay/collaboration workflows.
- Step 03/05 own current provider/stream capabilities; Step 09 owns only the
  future §38.33 capability rows not required by the first replacement slice.

The prohibition on spool/replay means no client/edge/remote offline spool,
replay log, or queued mutation. Server-side structural SSE cursor replay from
§38.22 remains required and is owned by Step 05.

## Toolchain contract

The current shell preflight confirms Python 3.12, Pylint, Ruff, and Pytest.
Mypy is not installed and the Black pyenv shim is broken; the first Python
implementation task must install and pin both. Rust is now installed through
Homebrew and linked as the rustup default `system` toolchain: rustc/cargo
1.97.1, rustfmt 1.9.0, Clippy 0.1.97, target `aarch64-apple-darwin`. A smoke
crate passed rustfmt, Clippy with `-D warnings`, and Cargo tests. Implementors
must still run the preflight in their own environment; failed installation is
recorded in `rewrite/blockers.md` while independent work continues.

## Final evidence

The finished rewrite must pass complete endpoint/event inventories, executable
schema generation and clean install, FK/quick checks, provider fixture parity,
legacy import parity, old/new behavioral comparison, performance gates,
security review, retention/backup/rollback, and every phase-specific gate in
the design.
