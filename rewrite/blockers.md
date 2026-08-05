# Rewrite blockers and deferred decisions

This file is the asynchronous handoff to the user. Agents must not ask the
user questions during autonomous execution. They must append a complete entry
here and continue all work that does not depend on the blocker.

## Status values

- `open`: blocks one dependency chain;
- `investigating`: evidence collection is in progress;
- `resolved`: the existing design or a recorded decision resolved it;
- `needs-user-decision`: the user must decide after reviewing the evidence;
- `wont-fix`: explicitly accepted by the design or user.

## Required blocker entry

Every entry must contain:

- unique blocker ID and first-seen UTC timestamp;
- affected task/coverage row, implementation commit, and current index status;
- exact design section/heading and line references;
- exact code/test/fixture/report paths and relevant command output;
- observed behavior and expected behavior;
- why the issue blocks progress and which dependent work is affected;
- all safe alternatives attempted, including their results;
- the narrowest decision needed, without proposing an unapproved redesign;
- autonomous next action and retry condition;
- owner agent and last-updated timestamp.

## Entries

No blockers recorded yet.

## BLOCKER-ORCH-001: Orchestrator cannot set per-agent reasoning-effort level

- Timestamps: 2026-08-06T00:00:00Z opened
- Affected: orchestration-level requirement in /goal directive ("implementor is always OPUS model with low effort, reviewer1 is always OPUS model with high effort level, reviewer2 is always codex model, manual tester is always a SONNET model with low effort"); not tied to a specific step/coverage row — applies to all steps 00-10.
- Design/code/test references: n/a (tooling constraint, not a design contradiction)
- Command output / observed behavior: The Agent tool available to the orchestrator only accepts a `model` parameter (sonnet | opus | haiku | fable). There is no reasoning-effort / thinking-budget / low-high parameter exposed. Effort is fixed by each agent's own definition frontmatter, not settable by the caller.
- Expected behavior: Orchestrator should be able to launch implementor agents as opus+low-effort and reviewer1 agents as opus+high-effort, distinct from each other.
- Impact: Cannot mechanically guarantee the requested effort differentiation between implementor and reviewer1 (both would run as plain "opus" with whatever default effort their agent definition uses). Model selection (opus/codex/sonnet) itself IS controllable and will be honored exactly.
- Attempted alternatives: Checked Agent tool schema; confirmed only `model` field exists, no effort/reasoning field.
- Required decision: Accept model-only differentiation (opus for implementor and reviewer1, codex agent for reviewer2, sonnet for manual tester) as the closest compliant substitute, OR provide an effort-capable agent definition/tool.
- Next action: Proceeding with model-only control per binding continue-independent-work rule; this blocker does not halt the dependent chain since model selection is still correct and differentiated by role.
- Retry condition: If an effort-parameterized agent/tool becomes available, re-launch future steps' implementor/reviewer1 pairs with explicit low/high effort.
- Owner: orchestrator

## BLOCKER-STEP00-001: Design has no OpenCode edge/plugin contract

- Timestamps: 2026-08-05T17:20:58Z opened
- Status: `open` (does not block step 00; work completed with a documented decision)
- Affected: step 00 (rust-shims-and-harness), implementation commit 2272f87 in
  /Users/z.yermagambet/code/personal/baqylau2
- Design references: §10.3 line 1615 promises "The Codex and OpenCode manifests
  provide their own exact key tables and fixtures", but no such OpenCode table
  exists anywhere in the document. §38.4 (lines 4857-4962) specifies the
  provider-edge subscription manifest, trust, and installation workflow only in
  terms of Claude Code hooks and a status line. §41.1 (lines 13856-13887) gives
  Claude Code an explicit per-family hook manifest; §41.2 does the same for
  Codex. There is no equivalent section for OpenCode. §38.37.8 (lines
  10912-10944) covers OpenCode *record mapping* only. §29 line 3811 lists
  `opencode/` as a bundled Python provider adapter package.
- Observed behavior: The design specifies, for OpenCode, only how native records
  map to canonical entities. It does not specify the edge format, the plugin
  contract, the subscribed native event families and their
  observational/answerable/delegating classification, the measured native
  deadlines, or the allowlisted environment-key table.
- Expected behavior: An OpenCode section analogous to §41.1/§41.2, naming the
  subscribed families, their classification and deadlines, and the exact
  environment-key table, plus a statement of the edge's packaging format.
- Impact: None on step 00. The task bundle explicitly required an OpenCode
  TypeScript plugin, so the work proceeded on an engineering decision rather
  than halting. It does affect any later step that must validate OpenCode edge
  behavior against a normative contract.
- Attempted alternatives: (1) Searched the whole design for an OpenCode manifest
  or key table; only the mapping section and the unfulfilled §10.3 promise
  exist. (2) Checked the legacy repository for an OpenCode adapter to mirror;
  `plugins/` contains only `claude_code`, `codex`, and `otel`, so there is no
  prior implementation to match either.
- Decision taken (documented, not approved): Implemented the edge as a
  TypeScript plugin matching OpenCode's real runtime contract
  (`@opencode-ai/plugin@1.14.39`, `Plugin`/`Hooks`), because OpenCode exposes no
  command-hook surface that a Rust executable could subscribe to, so the
  Claude/Codex edge shape is not available. Classified `event`,
  `chat.message`, `tool.execute.before`, `tool.execute.after`, and
  `command.execute.before` as observational; classified `permission.ask`,
  `chat.params`, and `chat.headers` as delegating and left them disabled with no
  handler registered at all, since each can mutate `output` and thereby take
  over a decision. Environment allowlist was chosen as OPENCODE_CONFIG,
  OPENCODE_CONFIG_DIR, OPENCODE_MODEL, OPENCODE_AGENT, PWD, OLDPWD,
  KITTY_LISTEN_ON, mirroring the shape of Claude's registered table.
  Code: crates/baqylau-edge/src/manifest.rs (OPENCODE_ROWS),
  crates/baqylau-edge/src/provider.rs (env_allowlist),
  plugins/baqylau-opencode/src/index.ts.
  Fixtures: fixtures/providers/opencode/{registered_observational_event_is_captured,
  delegating_permission_ask_fails_closed,unregistered_observational_becomes_generic}.
- Required decision: Confirm or replace the classification and environment-key
  table above with a normative OpenCode section, and state whether the OpenCode
  edge is a TypeScript plugin or a bundled Python adapter as §29 implies.
- Next action: None pending; step 00 is complete. Later OpenCode work should
  treat the above as provisional until the design section exists.
- Retry condition: Re-validate the plugin against the design once an OpenCode
  edge/manifest section is added.
- Owner: impl-00 (step 00 implementor)
- Last updated: 2026-08-05T17:20:58Z
