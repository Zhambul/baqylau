# Step 06 — Projections and machine services

Status: pending. Update `rewrite/index.md` whenever this changes.

## Objective

Build durable read projections and machine-wide services without prematurely
owning external controls.

## Required reading

Read the whole design. Primary sections: §§16–19, §§20–21, §§23–26,
§§30.4–30.7, §§38.10, §§38.15–38.19, §§38.23, §§38.26, §§38.30–38.31,
§§40.1–40.4, §§41.4–41.5, §§42.1–42.3.

## Implement

- attention truth/arm/delivery, transitions/reasons, asking/working/done/
  blocked/unknown, compaction, login/logout, notifications, interactions;
- alert precedence, held intents, timers, presence/device activity, composing,
  mute, tab focus, toast/push/Telegram/public links, retraction;
- usage authority/credit/rollups, cache categories, pricing, scoreboard/Σ/cost,
  mail census, Insights/Pulse, active time;
- tasks, titles, context/model/effort/runtime, quotas, account state, git,
  resources/memory, input buffers, drafts, badges, project grouping;
- OTLP, health/anomalies/suppressions, diagnostic telemetry, frontmost poller,
  slots, capability absence, workers, ports, DTOs, retention, restart recovery.

## Completion gate

Old/new observable agreement is sustained through restart, loss, suppression,
unknown data, pruning, and provider/account changes.
