# Step 09 — Handover, collaboration, and future features

Status: pending. Update `rewrite/index.md` whenever this changes.

## Objective

Implement full future-feature contracts after the current-provider path is
stable, preserving full scope, one SQLite database, and no spooling/replay.

## Required reading

Read the whole design. Primary sections: §§24–25, §§33–35, §§38.33,
§§38.36–38.39, §§40.5, §§41.3, §§41.6, §§42.1–42.4, §43, and every future
workflow DDL/OpenAPI/port/event/security/retention/fixture section.

## Implement

- handover compiler/package/workspace verification, target delivery,
  acknowledgement, divergence, checkpoints, backup/restore;
- collaboration invitations, roles/memberships, peer/task messages,
  actor-addressed delivery, and notification/security effects;
- public links/deep links, remote connected-only backend, mTLS, certificate
  rotation/revocation, capabilities, and file transfer;
- relay tap and untrusted subprocess plugins: JSON-RPC framing, limits,
  deadlines, capabilities, health, shutdown, typed contributions, and
  `routes=[]` extension boundary;
- all future tables, endpoints, DTOs, events, ports, retention, generated
  artifacts, migration/rollback and acceptance fixtures.

## Completion gate

Every future feature has executable schema/API/fixture ownership and one
vocabulary. Remote agents have no canonical database, spool, offline queue, or
replay log. Untrusted extensions cannot become privileged or add arbitrary
routes. No future feature is silently omitted.
