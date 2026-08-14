# Step 10 — Migration, parity, and cutover

Status: pending. Update `rewrite/index.md` whenever this changes.

## Objective

Run the strangler migration, prove old/new agreement, replace each plane, and
remove legacy code only after rollback and archived-history compatibility pass.

## Required reading

Read the whole design. Primary sections: §§29–32, §§38.3, §§38.18–38.20,
§§38.28–38.30, §§38.35–38.39, §§40.5–40.6, §§41.5–41.6, §§42.1–42.4,
§43, and all migration, parity, import, rollback, performance, security,
retention, and deletion-law sections.

## Implement in order

- dual observation/read comparison with every difference explained;
- seven-day parity and compound-load evidence for each plane;
- import parked DBs, audit/preferences/counters/KV, OTLP, errors, alerts,
  mutes, hidden groups, tasks, namespace preferences, drafts, scorebar,
  accounts, provider artifacts, and unknown-row quarantine;
- Phase 2a read-only replacement, then streaming, projections, controls,
  providers, handover, collaboration, extensions, and future planes;
- preserve archived-history read/import compatibility and declared sunset;
- disable each old plane only after owner, evidence path, mapper/domain/
  presenter fixtures, adapter tests, crash/uncertainty behavior, performance
  threshold, security review, accepted differences, and rollback exist;
- remove legacy code only after backup, generated-artifact validation, clean
  install, retention/GC, supervisor upgrade/rollback, and cutover gates.

## Final gate

All endpoints/events/DDL/ports/adapters/services/models/fixtures are complete;
differences are explicit; no feature is lost; no client/remote spool appears;
daemon outage remains visible and honest; archived history remains readable;
performance/security/retention/backup/rollback pass; and the root index is
marked completed.

The final-law audit checks every numbered law in §33, every tradeoff and
deferred-promotion trigger in §§34–35, every residual risk/schema lock in §36,
every architecture/package rule in §29, and every test category and scenario
in §30. No cutover checkpoint may be marked complete while a generated
spec-coverage row lacks evidence.
