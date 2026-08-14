# Step 03 — First vertical slice

Status: completed. Update `rewrite/index.md` whenever this changes.

## Objective

Prove the architecture end to end with one provider and minimal breadth.

## Required reading

Read the whole design. Primary sections: §§1–4, §§6–9, §§11–15, §§21–23,
§30.6, §32, §§38.1–38.9, §§38.12–38.14, §§38.20–38.22, §§38.31–38.32,
§§40.6, §§41.1–41.2, §§41.5, and §§42.1–42.2.

## Implement

- Claude observational edge event through authenticated ingestion,
  Observation/provenance/quarantine, and provider mapping;
- Conversation, actor track, Node/parts, AgentSession/attempt/aliases,
  Operation/detail, Stream, links, native positions, epochs, and head rules;
- one Claude decoder with exact hook authority, prompt/assistant/tool records,
  Task correlation, failure closers, compaction, and generic unknown records;
- ActivityComposer backlog and one semantic assistant Stream;
- one command-output Stream with prepare-then-answer, tee/staging, source
  reader, bounded output, sanitization, cancellation, uncertainty, and later
  reconciliation;
- snapshot API and minimal read surface for committed/provisional content,
  Operations, actor scope, and health;
- restart, crash, daemon-unavailable, and pass-through behavior.

## Completion gate

Tree/head correctness, Activity ordering, identity mapping, stream recovery,
synchronous fallback, mixed-load SQLite performance, provider/terminal
independence, and crash isolation pass without controls or legacy deletion.
