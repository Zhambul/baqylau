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
| 02 | [Foundation and storage](02-foundation-and-storage/task.md) | completed | 01 | code: baqylau2@d3c9e95; migration: schema.sql 5 ordered units, migrations/0001_v4_clean.json (restore_required), generate_schema_artifacts.py --check clean (152 tables/93 indexes/127 triggers, quick_check ok, foreign_key_check no rows); manifest: storage_matrix.json (152 rows, 9 required cells + citation each), schema_catalog.json, docs/storage-matrix.{md,csv}; tests: 759 passed (pytest), black/ruff/mypy --strict/pylint (10.00/10, 0 E/F under --enable=all) all clean, cargo build --offline clean (Rust untouched); reviews: 2 independent passes, both approved after 2 fix rounds (found/fixed 6 blockers, 11+ majors including 2 real concurrency bugs, a segfault, a lease-recovery gap, and an exploitable missing lease guard); manual tests: daemon CLI smoke pass with notes, 2 crash bugs found and fixed (corrupted DB and unwritable data-root now fail with structured errors, not tracebacks); rollback: daemon upgrade/backup/rollback implemented per §26.3, launchd/systemd supervisor with crash-loop detection; performance: §40.6 storm-profile gate UNMEASURED (harness built and tested, 5 attempts made, all either measured stale code/harness bugs or were abandoned per operator instruction — no numbers exist; writer-admission/transaction-latency/WAL-peak/composer-churn gates remain open, tracked as untracked risk, not silently passed); blockers: BLOCKER-STEP02-001 (§38.34 frame header field-list sums to 104 bytes, design prints header_len=84; implemented as 104), BLOCKER-STEP02-002 (phase0/README.md prints stale design digest) |
| 03 | [First vertical slice](03-first-vertical-slice/task.md) | completed | 02 | code: baqylau2@4c5d714 (branch step-03-first-vertical-slice: fea454e, 797ccd9, 7bd0d1b, 4c5d714); manifest: 26 new modules (decoder, answerable adapter, canonical transaction, snapshot queries, ActivityComposer, edge socket, source readers), storage matrix now 31/152 tables with implemented methods; tests: 1111 passed (unit 116, contract 193, integration 93, e2e 98, architecture 595, diagnostics 16), black/ruff/mypy --strict/pylint (10.00/10, exit 0) all clean, cargo test/clippy clean, generate_schema_artifacts.py --check clean; reviews: 2 independent passes, both approved after one fix round (found/fixed 3 blockers — scrambled activity ordering with a tautological test, unwired §11.1 observation invariant, silent permission-escalating command rewrite now default-off — plus 4 majors including a 429-error-as-committed-answer bug and a recovery step reporting false success); manual tests: end-to-end ingestion smoke pass with notes, 1 real crash bug found and fixed (restart with an open Stream crashed with KeyError, now recovers cleanly); performance: shape gates only (~10x looser than §38.30, catches order-of-magnitude regressions, not absolute compliance — self-disclosed, not oversold); rollback: n/a beyond Step 02's daemon rollback (no new migrations); blockers: BLOCKER-STEP03-001 (operations table has no native_operation_key column; app-level idempotency only), BLOCKER-STEP03-002 (no per-edge socket secret; SO_PEERCRED/LOCAL_PEERCRED peer-uid auth substituted, residual risk accepted per §25.3's "where possible"), BLOCKER-STEP03-003 (MessageDisplay and transcript-record assistant identities are uncorrelated; dormant until a future step wires the transcript reader live, pinned by a test that fails if they ever converge without a deliberate change), BLOCKER-STEP01-003 carryforward (5 hook families §41.1 omits, now registered observationally) |
| 03a | [Contract blocker resolution](03a-contract-blocker-resolution/task.md) | pending | 03 | code: —; migration: —; manifest: —; tests: —; rollback: —; resolves the binding contradictions recorded in §43.4 |
| 04 | [Canonical read model](04-canonical-read-model/task.md) | completed | 03a | code: baqylau2@cdf54a1 (branch step-04-canonical-read-model); manifest: 26 statements through the sealed registry, 6 real read stores, storage matrix 31→36/152 implemented tables, READ_ENDPOINTS generated from phase0/inventory/endpoints.json (41 endpoints, drift-checked); serving: real AF_UNIX read HTTP server wired into the composition root, reusing edge_socket peer-uid auth, 0600 socket; tests: 1773 passed, black/ruff/mypy --strict/pylint (10.00/10, exit 0) all clean, generate_read_endpoints.py --check and generate_schema_artifacts.py --check clean; reviews: 2 independent passes across 3 rounds, both approved (round 1 found a decisive scope gap — zero storage/HTTP wiring — which the implementor resolved by doing the real wiring rather than rescoping; found/fixed 6 blockers including a write-through-the-public-read-API security hole (PRAGMA query_only bypass, including a paren-form variant caught in round 3) and 12 majors including two "fabricated design quote"/regression bugs matching a real historical legacy incident (522,826-vs-8,969 tokens), plus 2 more blockers and 7 majors surfaced by the storage/HTTP wiring itself, all fixed and mutation-verified); manual tests: pure-function pass + live-socket re-test, both pass (real seeded round-trips, clean typed errors for malformed/unknown/traversal input, no crashes); rollback: n/a (no new migrations beyond Step 02's); blockers: BLOCKER-STEP04-001 (§38.9 provider name in fold table; symbol-scoped cited exception, open), BLOCKER-STEP04-002 (§38.2 compact-boundary fail-open deliberately inverts legacy default; resolved by design, tested against legacy's real measured numbers), BLOCKER-STEP04-003 (getLimits missing from generated endpoint inventory; needs-user-decision, expected to resolve once 03a lands — a test fails by design at that moment to force the workaround out), BLOCKER-STEP04-004 (conversations.active_agent_session_id has no writer, so conversation_title_current can never hold a provider_live revision; open, owner Step 06), BLOCKER-STEP03-001/002/003 carryforward unaffected |
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
