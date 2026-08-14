# Step 08 — Providers, backends, and accounts

Status: pending. Update `rewrite/index.md` whenever this changes.

## Objective

Add provider modes and backend/account capabilities without changing generic
core or surface code.

## Required reading

Read the whole design. Primary sections: §§10–12, §§20–21, §§26–27,
§§30.5–30.6, §§35–38, §§38.2, §§38.4, §§38.6–38.7, §§38.17–38.18,
§§38.35–38.39, §§40.5, §§41.1–41.3, §§41.5, §§42.1–42.3.

## Implement

- Claude hooks, installation/trust/upgrade/revert, exact env keys, answerable/
  delegating fail-closed, observational unverified-build generic mapping,
  OTLP/statusline, and account launch;
- Codex rollout/app-server parsing, child boundaries/admission, synthetic and
  plan rules, non-shell tools, web search, standalone host, sidecar slug/claim,
  model/effort/pricing;
- OpenCode/programmatic modes and capability contracts;
- local/remote backends, execution targets, connected-only remote protocol,
  mTLS, liveness, file transfer, no replay;
- principals, browser sessions, credentials, certificates, invitations, roles,
  CSRF/origin, credential broker, quotas, relimit, migration, fallback ladder;
- provider-specific fixtures and ports with no provider knowledge in core.

## Completion gate

Unsupported answerable/delegating builds fail closed; observational builds
produce generic evidence with provenance. Account migration, remote disconnect,
credentials, quotas, launch, and provider parity recover safely.

## Required provider rows

Claude rows must include SessionStart/End, InstructionsLoaded, UserPromptSubmit,
PreToolUse, PostToolUse, PostToolUseFailure, PostToolBatch, Stop, StopFailure,
Notification, PreCompact, PostCompact, TaskCreated/Completed,
SubagentStart/Stop, every field/class/deadline, `PermissionRequest` audit-only
semantics, `CLAUDE_MIRROR_LIVE_FG_SUB`, `KITTY_LISTEN_ON`, and observational
unverified-build behavior. Codex rows must include filename-time admission,
thread-source discovery, replay boundary, synthetic/tag/plan rules,
non-shell exec, web_search_end, standalone-host lifecycle, native child
register, sidecar slug/claim, and model/effort/pricing. No row may be replaced
by “provider-specific behavior.”
