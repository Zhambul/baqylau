# Step 01 — Inventory and fixtures

Status: completed. Update `rewrite/index.md` whenever this changes.

## Objective

Create the measured implementation baseline before replacing any legacy plane.
Produce inventories, fixture corpora, parity oracles, performance profiles,
security/retention decisions, and migration rollback contracts. Do not
implement production controls or delete legacy code in this step.

## Required reading

Read the whole v4 design. Primary sections: §§0–7, §§10–12, §§20–21,
§§26–32, §§34–37, §§38.3–38.4, §§38.18–38.19, §§38.29–38.30,
§§38.35–38.39, §§40.5–40.6, §§41.5–41.6, §§42.1–42.4, and §43. Also read
every referenced provider, terminal, storage, API, DDL, SSE, DTO, fixture,
performance, security, retention, and failure section. The review documents
and their cited legacy files are evidence, not optional background.

## Deliverables

- Feature-to-owner matrix for every legacy feature, environment key, tab
  registry row, hook family, provider artifact, terminal role, endpoint, SSE
  event, table/index/trigger, worker, retention class, and future feature;
  each item is implemented, imported, explicitly dropped, or blocked with a
  source citation and failed alternative.
- Frozen raw fixtures for Claude, Codex, OpenCode, kitty/terminal, dashboard,
  OTLP, alerts, usage, mail, memory, accounts, migrations, parked databases,
  audit/preferences/counters/KV, malformed inputs, and unknown provider builds.
- Fixture metadata: provider/build, OS, cwd/worktree, account/profile,
  config directory, exact environment snapshot, terminal endpoint, source
  epoch/ordinal, expected authority, uncertainty, and retention class.
- Old-system parity commands and golden outputs for identity, start/end/resume/
  adoption, branches, compaction, child/team/sidecar order, tools, streams,
  attention, usage, tasks, titles, accounts, controls, alerts, presence,
  drafts, imports, and all render modes.
- Compound-load/storm profiles covering hook deadlines, SQLite admission, blob
  and CAS cost, triggers, feed publication, GC, rendering, remote bandwidth,
  and 20-live-Conversation traffic.
- Security, retention, backup, supervisor, install, upgrade, rollback,
  offline/crash-loop, and no-spool decisions as executable checks.

## Completion gate

No unexplained legacy feature, constant, event, endpoint, table, adapter,
environment key, or fixture remains. Phase 0 can run on another machine and
all artifacts are immutable inputs to Step 02 and later parity tests.

## Required manifest outputs

Create machine-readable inventories (and checked-in human-readable renderings)
for every endpoint, event/live frame, DTO/model, SQL object, service/port,
worker, provider hook, terminal role, environment key, fixture, gate, and
retention class. The endpoint and event inventories must be set-equal to the
design's operation/event manifests; the schema inventory must include all five
ordered SQL units and the generated object counts; the ownership inventory must
have no unowned row. Include exact source section/line citations and a
`failed_alternative` field for each rule.

The test matrix must assign every §30 architecture/import/domain/property,
projection/presentation, adapter, end-to-end, and parity test plus every
§§38.31, 40.7, 41, 42, and 43 review fixture to one step. Each row records
fixture ID, setup, stimulus, expected durable rows, DTO/event/render result,
failure/unknown branch, performance bound, and cleanup/rollback action.
