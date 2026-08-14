# Step 05 — Streaming and rendering

Status: pending. Update `rewrite/index.md` whenever this changes.

## Objective

Implement source readers, semantic streams, rendering, SSE/live facets, and
terminal/web presentation with exact recovery and bounded behavior.

## Required reading

Read the whole design. Primary sections: §§8–9, §§12–15, §§17, §§20, §§22–23,
§§30.5–30.6, §§38.4–38.10, §§38.20–38.22, §§38.30–38.31, §§40.3–40.4,
§§41.5, §§42.1–42.2, and every renderer, tailer, viewport, terminal, SSE,
live-frame, sanitization, DTO, and fixture section.

## Implement

- Claude/Codex/OpenCode cursors, inode/truncation/tailer checkpoints,
  foreground/background/monitor/subagent/sidecar ownership and backstops;
- prepare-then-answer, tee capture, transforms, output caps, ANSI/OSC safety,
  formatter/highlighter/cache, multi-resource output and code formatting;
- semantic blocks, activity classes/register/audience, folds, focus provisional
  replies, summaries, copy/expand links, and mail rules;
- web/terminal renderers, cells/tabs/wrap, scorebar, pane geometry, viewport
  DSR/anchor/restore/drift, kitty socket/bindings;
- durable SSE feeds, cursors, replay/resnapshot, coalesced overview budgets,
  live facet frames, connection/upgrade frames, auth/revision reducers;
- view mode, foreground elapsed, compaction, suggestions, terminal presence,
  own-echo suppression, reconnect, and stale-state behavior.

## Completion gate

Fidelity, recovery, sanitization, resize, viewport, binding, stream copy/range,
web reconnect, SSE replay/resnapshot, bandwidth, first-paint, append latency,
and compound-load gates pass.

## Non-negotiable constants and fixture rows

Implement the exact caps: prompt 24 lines, teammate message 24, outgoing
SendMessage 12, generic tool request 10, command body 60, job note 8, and
uncapped agent MESSAGE/RESULT. `POLL_S=0.4` and `BACKSTOP_S=21600` belong to
the shared tailer; `FG_BACKSTOP_S=7200` belongs only to Claude foreground
capture; `CLEAR_GAP_S=0.15` belongs to bracketed-paste clearing; and
`COMPACT_MAX_S=900` is read-side compaction display expiry, not a provider
writer timer. Apply formatter/ANSI/OSC rules and scrollback env default 4800;
viewport DSR
arrival-only, 400-row gross miss, three total passes, 0.7 settle, two drift
corrections, five-row slack, eight-second watch; four-cell pane step, 25%
bias, five-row scorebar; `N of M shown`; verbose/default/focus fold tables;
cell-width/tabs-to-8/wrap/reassertion/cache identity. Every constant has the
matching fixture and failed alternative from §§38.8–38.10, 40.3–40.4, 41.5,
42.2.
