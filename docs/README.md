# Design docs

The authoritative, exhaustively-detailed record of how every mechanism works
*and why the alternatives failed*. When changing behavior, update the matching
doc here in the same commit — the "why not X" notes are what prevent regressing
to already-rejected designs.

| Doc | What it covers |
|---|---|
| [tab-colors.md](tab-colors.md) | The tab-colour state machine — colour table, dispatch modes, background/agent detection at `stop`, cancelled-turn recovery (`interrupt-watch`), notes/tweaking |
| [architecture.md](architecture.md) | The core / plugins / frontends layering, every module's responsibility, the dependency rule, entry-shim filenames, compat shims |
| [styleguide.md](styleguide.md) | The normative rules: layout/naming, the dependency rule, single-owner vocabularies, import-time purity, audit-before-swallow, SQL/db discipline, refactoring and test conventions |
| [wiring.md](wiring.md) | Everything outside the repo: kitty.conf, open-actions.conf, the full `~/.claude/settings.json` hook table (`claude-hook.py` dispatcher routing), telemetry env, codex host wiring, the pyenv retarget, activation/smoke test |
| [mirror-pane.md](mirror-pane.md) | The command mirror pane — block anatomy, chips/palettes/gutters, command pretty-printing, markdown/JSON/YAML/source rendering, the renderer + reflow, file-op one-liners, pane lifecycle (`claude-split.py`), resume/adoption, anchoring, sizing/keys |
| [click-to-view.md](click-to-view.md) | ⧉ copy links, click-to-view expansion of Read/Update/Write lines, viewport anchoring/restore, drift watch, paint-time neutralization |
| [streaming.md](streaming.md) | How command output streams live — fg tee-rewrite (`updatedInput`), bg/monitor tailers, redirected output, Ctrl+B hand-off, cancelled commands, completion detection per kind |
| [subagents.md](subagents.md) | Subagent/teammate transcript streaming, ctx fill + usage rollup, crash-safe spend reconciliation, model·effort tags, every subagent end-shape (cancel/reject/API-error/quit), agent teams |
| [scoreboard.md](scoreboard.md) | The 5-row scoreboard window — session id, ✉ message census, activity row, Σ token breakdown, pricing table |
| [otel.md](otel.md) | The OTEL cost pipeline — the singleton OTLP receiver, why OTEL over transcript folding, the SessionEnd fold fallback |
| [codex.md](codex.md) | Codex streams — the per-session watcher, companion jobs + native rollouts, the standalone codex host |
| [audit.md](audit.md) | The always-on SQLite audit trail — tables, the universal subscriber, the CLI |
| [sessionapi.md](sessionapi.md) | The read-side session-data API — presentation channel vs read model, the `streams`-table keystone, fork-aware `sid_chain`, the transcript parse/paint split + `plugins.activity()`, the fidelity ladder, why not an events table |
| [dashboard.md](dashboard.md) | The web dashboard — the `dashboard/` consumer tier, the ops→HTML presenter (escape-as-neutralize), server/SSE design, the notification watcher, singleton lifecycle, the Hermes-derived theme system |
| [remote.md](remote.md) | Remote access — cloudflared tunnel + Cloudflare Access in front of the dashboard, the `CLAUDE_DASH_ORIGINS`/`CLAUDE_DASH_READONLY` knobs, the threat model (the control plane is RCE), rejected exposure shapes |
| [testing.md](testing.md) | The hermetic e2e suite and its test-only env knobs |
