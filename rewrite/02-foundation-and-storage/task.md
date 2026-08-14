# Step 02 — Foundation and storage

Status: completed. Update `rewrite/index.md` whenever this changes.

## Objective

Build the supervised daemon and durable machine-wide SQLite foundation without
production external effects.

## Required reading

Read the whole design. Primary sections: §§2–5, §§10.1–10.3, §§20.1–20.2,
§§26.1–26.3, §§27–29, §§33–35, §§38.25–38.27, §§38.34–38.36, §§38.39,
§§40.7–40.8, §§41.3–41.6, §§42.3–42.4, and all DDL, SQL ordering, OpenAPI,
authentication, storage, retention, and migration sections.

## Implement

- launchd/systemd supervisor, login startup, restart/backoff, crash-loop state,
  visible health banner, offline status/logs, upgrade/backup/rollback;
- composition root and hexagonal dependency direction;
- one SQLite database, migrations, deferred FK ordering, WAL/transaction
  policy, read/write connections, backup, `foreign_key_check`, `quick_check`,
  and generated schema digest;
- every authoritative Foundation, review, future-workflow, and second-review
  table/index/trigger: principals/auth/browser/certificates/invitations,
  backends/targets/accounts/pricing, Conversation/tracks/Nodes/Operations/
  Streams, evidence, blobs/references, resources, preferences, outbox/effects,
  health/repairs, retention, notifications, usage, views, terminal bindings,
  actor scoreboards, OTLP, memory, and imports;
- storage ports and transaction boundaries, read views, retention workers,
  blob reachability, CAS frame/chunk rules, and torn-tail recovery;
- exact provider environment snapshots, secret exclusion, provenance, source
  registrations, terminal endpoint hints, and configuration layering;
- diagnostics, ingestion gaps, quarantine, anomaly catalogue, suppressions,
  audit, health, offline, and no-spool outage semantics.

## Completion gate

Clean install executes every SQL unit; generated schema/API registries resolve;
FK/check/trigger checks pass; restart preserves evidence; poison rows isolate;
retention and backup restore; no external effect is emitted. Record the Step 01
storm-profile baseline before proceeding.

## Mandatory concrete storage matrix

For every table and index in the schema manifest, write the actual storage
methods, read key/order, transaction boundary, lock/lease/CAS predicate,
idempotency key, revision source, FK behavior, retention owner, and emitted
event. Cover the Conversation/track/Node/Operation/Stream component;
observations/consumers/quarantine; blobs/references/GC; resources/versions;
input buffers/preferences; outbox/effect attempts/leases;
attention/arms/notifications/deliveries; usage/credit/authority/rollups;
health/anomalies/suppressions; accounts/quotas/migrations;
terminal/panes/paint; actor facets/scoreboards; memory/tasks/drafts/overview/
git; OTLP; auth/certificates/invitations; collaboration/handover/remote/plugin
tables; and every future workflow table. No ad-hoc SQL query or hidden commit
is allowed outside this matrix.

Also implement the package tree/import-direction checks from §§3–5 and §33:
domain imports no adapters, application imports ports not providers, adapters
own provider/terminal knowledge, and the composition root is the only wiring
owner.
