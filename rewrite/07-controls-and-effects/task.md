# Step 07 — Controls and effects

Status: pending. Update `rewrite/index.md` whenever this changes.

## Objective

Implement destructive controls and external effects with durable ownership,
leases, attempts, receipts, reconciliation, and capability-driven UI.

## Required reading

Read the whole design. Primary sections: §§8, §§11–12, §§17, §§20, §§22,
§§24–26, §§30.5–30.6, §§38.4–38.16, §§38.20, §§38.28–38.31,
§§40.4–40.6, §§41.4, §§42.1–42.4.

## Implement

- outbox, attempts, leases, idempotency, receipts, retries, indeterminate
  state, reconcilers, sagas/checkpoints;
- RuntimeDriver and terminal roles: fresh binding, input mode, bracketed paste,
  clear gap, motion, interrupt/take-back, interactions, plan menus, DSR,
  viewport, pane resize, tab paint, scorebar;
- message delivery, queue evidence, resume/park/relaunch, optimistic bubbles,
  interactions, rewind two-plane workflow;
- notification routes/deliveries, push collapse, Telegram, terminal reservation,
  public links and retraction truth;
- programmatic controls, account migration/relimit, provider launch,
  credential, upload/clipboard/dictation effects;
- CSRF/origin, read-only recheck, auth, input occupancy, audit, telemetry,
  cancellation, recovery, and no-spool outage behavior.

## Completion gate

Each exclusive effect plane has one durable owner/lease. Destructive actions
prove bindings, record requested/effective state, handle unknown safely, and
pass control, security, performance, reconciliation, and rollback fixtures.
