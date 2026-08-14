# Step 04 — Canonical read model

Status: pending. Update `rewrite/index.md` whenever this changes.

## Objective

Complete provider-independent history and read models for live and parked
sessions while legacy reads remain authoritative.

## Required reading

Read the whole design. Primary sections: §§1–4, §§6–9, §§13–15, §§18–19,
§§21–23, §§30.1–30.4, §§38.1–38.3, §§38.6–38.9, §§38.13, §§38.17–38.23,
§§38.31–38.33, §§40.1–40.4, §§41.2, §§41.4–41.5, §§42.1–42.2, and all
DTO/OpenAPI/read-storage sections.

## Implement

- Conversation/Node/Operation/AgentSession/actor-track projections;
  dialogue/work/causal/runtime relationships;
- native indexes, aliases, attempts, parts, links, checkpoints, source epochs,
  compaction/revert graph, adoption/resume/fork;
- provider-owned title/model/effort/context/account/runtime facts;
- ActivityComposer, generations, placement, actor scope, hidden husks,
  correction, whole-block pagination;
- overviews, sessions, resume search, git/dirty state, tasks, memory, stats,
  scoreboard, resources, drafts/preferences, and errors;
- exact DTOs, queries, pagination, auth scopes, freshness/unknown semantics,
  all read endpoints, and structural SSE replacement reducers.

## Completion gate

Parity covers late records, child/team/sidecar scope, interactions, compaction,
titles, branches, pagination, view modes, and resume search. Read endpoints
have declared audit exceptions (`web-copy`/`web-view`) and no accidental writes.
