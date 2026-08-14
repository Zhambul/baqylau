# v4 specification coverage manifest

This file is the machine-checkable coverage contract for the implementation
tasks. It deliberately does not copy the design into a second conflicting
specification. The design remains the source of truth; this manifest assigns
each anchor to an implementation step and requires generated inventories for
all concrete contracts.

## Section ownership

| Design anchors | Owning step | Required evidence |
|---|---|---|
| §§0–2 | 01, 02, 10 | decision/risk ledger; persistence and constraint tests |
| §§3–6 | 02, 10 | package/import graph; composition-root and transaction tests |
| §§7–9 | 03, 04, 05 | domain/storage/ingestion/stream fixtures |
| §§10–12 | 03, 05, 07, 08 | provider/edge/driver manifests and deadline tests |
| §§13–15 | 03, 04, 05 | identity/branch/activity/stream parity |
| §§16–19 | 04, 06, 07 | storage/query/projection/effect ownership and tests |
| §§20–22 | 05, 07, 08 | terminal/account/backend adapter contracts |
| §§23–25 | 08, 09 | handover/extension/collaboration/security tests |
| §§26–27 | 02, 05, 06, 10 | recovery/degradation/performance evidence |
| §28 | 01, 02, 10 | superseded inventory checked but never executed |
| §§29–30 | 01, 02, 10 | package checks and complete test matrix |
| §§31–32 | 01, 03, 10 | phase/vertical-slice/cutover evidence |
| §§33–37 | 01, 02, 10 | law/tradeoff/risk/final-architecture audit |
| §§38.1–38.7 | 03, 04, 05, 08 | actor/context/import/provider/correlation authority |
| §§38.8–38.14 | 05, 07 | stream/render/terminal/control fixtures |
| §§38.15–38.19 | 06, 07 | presence/alerts/usage/audit/health evidence |
| §§38.20–38.24 | 04, 05, 06 | parking/activity/SSE/overview/API inventory |
| §§38.25–38.27 | 02 | storage ports and executable SQL |
| §§38.28–38.30 | 01, 02, 05, 10 | retained-complexity and performance gates |
| §§38.31–38.32 | 01, 10 | fixture traceability and contradiction audit |
| §38.33 | 09 | relay/remote/plugin future contracts |
| §§38.34–38.35 | 02, 10 | physical storage, retention, migrations, digest |
| §§38.36–38.38 | 04, 07, 08, 09 | OpenAPI/auth/DTO/event/operation manifests |
| §38.39 | 02, 08, 09, 10 | future workflow DDL and rollback |
| §§40.1–40.5 | 04, 06, 07, 08 | tasks/memory/scoreboard/presentation/accounts |
| §40.6 | 01, 05, 07, 10 | migration/performance decision evidence |
| §40.7 | 02, 10 | executable fifth SQL unit and generated digest |
| §40.8 | 02, 04, 06, 07 | storage ports, services, live frames |
| §§41.1–41.2 | 05, 08 | Claude/Codex provider contract fixtures |
| §§41.3–41.4 | 06, 07, 08 | pricing/accounts/adoption/attention/presentation |
| §§41.5–41.6 | 01, 02, 05, 10 | retention/testing/schema completeness |
| §§42.1–42.4 | 04, 05, 07, 08, 10 | launch/copy/limits/auth/complexity contracts |
| §§43.1–43.3 | 01, 02, 05, 08, 10 | gate escalation/generated artifacts/fixtures |

## Generated contract inventories

The implementation must generate these from the authoritative design sections,
not maintain hand-copied lists:

1. **OpenAPI inventory** — every method/path, operation ID, auth scope,
   principal/device rule, path/query/header/body schema, response DTO/status,
   error code, idempotency/CAS/revision rule, storage owner, transaction,
   emitted event, and audit exception from §§38.24, 38.36, and 38.38.
2. **SSE/live inventory** — every durable feed/event pair and every live frame,
   payload field/bound, producer transaction, authorization revision, cursor,
   replay/resnapshot rule, reducer, own-echo rule, and fixture from §§38.22,
   38.36, 38.38, 40.8.
3. **Schema inventory** — every table/column/type/default/CHECK/FK/UNIQUE,
   index, trigger, migration unit/order, digest normalization, query key,
   write method, transaction, lock/lease/CAS, idempotency key, retention class,
   deletion law, and rollback rule from §§38.25–38.27, 38.34–38.35, 38.39,
   40.7.
4. **Port/service/worker inventory** — every protocol method, application
   owner, adapter capability, schedule/wake source, lease/backoff/batch,
   failure state, evidence row, event, and test from §§38.25–38.27, 38.37,
   40.8, 41, 42, and 43.
5. **Provider/terminal inventory** — every hook family/field/class/deadline,
   environment key, parser authority, child/sidecar rule, terminal role,
   socket/binding/probe/input/viewport constant, and provider fixture.
6. **Test/gate inventory** — every architecture, mapper, domain/property,
   projection/presentation, adapter, end-to-end, parity, security, retention,
   recovery, performance, and grouped review fixture named by the design.

No hard-coded endpoint/event/table count in this file is authoritative. The
generator must resolve the current design text and fail on duplicates, missing
owners, missing schema/API objects, or count disagreement. A task can be marked
completed only when its generated rows and evidence links are present.

## Row schema and generator contract

The checked-in generated artifact is `rewrite/generated/spec-coverage.json`.
Its input is the UTF-8/LF design file plus the frozen Step 01 fixture corpus;
the generator command is:

```text
python tools/rewrite_spec_manifest.py \
  --design docs/rewrite-design-v4-codex.md \
  --fixtures rewrite/fixtures \
  --owners rewrite/spec-coverage.md \
  --out rewrite/generated/spec-coverage.json \
  --check
```

Each row has exactly:

```json
{
  "id": "S-38.24|EP-<method>-<path>|EV-<feed>-<name>|DDL-<object>|PORT-<owner>-<method>|TEST-<id>|GATE-<id>",
  "kind": "section|endpoint|event|live_frame|dto|table|index|trigger|port|service|worker|provider|terminal|fixture|gate",
  "source": "design section and heading",
  "owner_step": "01..10",
  "depends_on": ["row id"],
  "status": "pending|in_progress|blocked|completed",
  "implementation": "path or commit",
  "migration": "version or null",
  "generated_artifact": "path and digest or null",
  "tests": ["fixture/test IDs"],
  "performance_security_retention": ["gate IDs"],
  "rollback": "evidence path or null",
  "failed_alternative": "required failure behavior"
}
```

`--check` fails closed if a design heading, endpoint, event/live frame, DTO,
SQL object, port/service/worker, provider/terminal rule, fixture, or gate has
no row; if a row has more than one owner; if endpoint/event/schema sets differ
from generated OpenAPI/SQL/SSE artifacts; or if an evidence field is missing
for a completed row. The generator must preserve exact source text/line
locators and must not infer omitted fields.
