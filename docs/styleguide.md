# Style guide

Normative rules for code in this repo. [architecture.md](architecture.md)
describes *what the pieces are*; this file says *how code must be written*.
Every rule here was extracted from a smell or bug that was actually found and
fixed — the rule exists so the same class doesn't come back. When a rule and
convenience conflict, the rule wins; when a rule genuinely can't apply, say so
in a comment at the site and in the commit message.

## Layout and naming

- **The repo root holds no Python files.** Executables live in `bin/`,
  importable code lives in the packages (`core/`, `plugins/<tool>/`,
  `frontends/`), tests in `tests/`, design docs in `docs/`.
- **Hyphen = executable, underscore = module.** A hyphenated `bin/claude-*.py`
  is an entry point and is un-importable *by design*; an underscored `*.py`
  inside a package is an importable module. This split IS the naming
  convention — do not "fix" it toward uniformity.
- **Entry basenames are frozen.** They are the audit DB's handler/script
  vocabulary (`hook_events.handler`, `errors.script`, spawn parents) and are
  referenced by external wiring (`~/.claude/settings.json`, kitty's
  `open-actions.conf`, `~/.codex/hooks.json`). Moving an entry is fine;
  renaming one forks the audit vocabulary — don't.
- **Entries are thin.** A `bin/` script is ~8 lines: `sys.path.insert` to the
  repo root, import the package module, call `entry()`. Implementation lives
  in the package. The two pane renderers (`claude-mirror.py`,
  `claude-scorebar.py`) are the sanctioned exception (assembly-layer scripts);
  even they share their skeleton via `core/panescript.py`.
- **Spawn siblings via `core/paths.py`'s `BIN`** (and derive the repo root
  from `paths.ROOT`) — never re-derive a path with `dirname(dirname(...))`.

## Layering (the dependency rule)

- `core/` imports only `core/`. `frontends/` import at most `core/`.
  `plugins/<tool>/` import `core/` + `frontends/`, **never another plugin**.
  `dashboard/` (the top consumer tier) imports `core/`, the `plugins` registry
  root, **and `frontends/`** (for its control plane — the two write endpoints
  reach the terminal through `frontends.get()`); nothing imports `dashboard/`
  except its bin/ entry and tests. `bin/` scripts may import anything.
- Surface shared by two plugins goes in `core/` (that's why `streamfmt.py`
  exists) — never solved by a cross-plugin import or by copy-paste.
- Terminals are reached only through the `Frontend` interface
  (`frontends.get()`). No code outside `frontends/` may touch a kitty-only
  attribute (`.listen`, `.kitten`, `frontends.kitty` internals) — use
  `export_env()` / the interface methods. `tests/test_l0_frontends_contract.py`
  enforces this; keep it passing, don't weaken it.

## Single-owner vocabularies — never re-encode

Each of these facts has exactly ONE owner. Using the value means importing the
owner; writing the literal again anywhere else is a bug (several owners are
backed by grep-style regression tests that will fail the build):

| Fact | Owner |
|---|---|
| `/tmp/claude-mirror-<key>.log` path format, `ROOT`, `BIN` | `core/paths.py` |
| Semantic colours `SLATE/ORANGE/RED/…`, `fmt_dur`, `kfmt`, `fmt_usd`, `split_tokens()`, `token_parts()` | `core/ops.py` |
| Tab states + `COLORS` hex table + tab-DB schema | `core/tabs.py` (read cross-module via `state.tab_state`) |
| Slot claim-token format (both directions: `_token`/`_untoken`) | `core/slots.py` |
| Stream-block shapes: `cap`, `chip`, `gutter`, `tok_rollup`, `file_line` | `core/streamfmt.py` |
| File-op display name (bare basename / `✎` scratchpad icon / dim out-of-project dir), incl. the scratchpad path pattern | `core/streamfmt.file_display` |
| Session-alive probe | `core/state.parked()` — a bare exists check, never a connect |
| Detached-spawn mechanics (DEVNULL stdio + `start_new_session=True` + the `spawn`/`error` audit rows) | `core/spawn.spawn_detached` — `hookkit.spawn_streamer` is its bin/-name-resolving wrapper |
| Mirror-pane width default (`DEFAULT_BIAS`, the `CLAUDE_MIRROR_BIAS` fallback both hosts share) | `core/hostpane.py` |
| Claude config dir default (`$CLAUDE_CONFIG_DIR` else `~/.claude`) | `plugins/claude_code/model.config_dir()` |
| Subscription-account vocabulary: the switcher's env contract, `accounts.tsv` registry, per-account config-dir layout (`configs/<slug>`) | `plugins/claude_code/account.py` — `current()`/`registry()`/`alias_for()`/`config_dir_for()` |
| Interactive-login-shell launch wrapper (`$SHELL -lic '<word> "$@"'`, `LAUNCH_SHELLS`) | `plugins/claude_code/account.launch_argv` (via the `plugins.launch_argv` fan-out) — the web launch and the rate-limit migration compose the SAME argv |
| Per-account usage read model: freshest-per-slug `usage`/`limit-hit` aggregation (the hit filed under its OWN stamped slug — a migrated session's DB carries the old account's stamp) + what-counts-as-a-window / rolled-over-window / effective-5h / limit-still-active / limit-bars-a-migration-target arithmetic | `core/sessionapi.py` — `account_usage()`/`usage_windows()`/`window_span()`/`_window_rolled()`/`effective_five_hour()`/`effective_usage()`/`limit_hit_active()` (time only — the dashboard pill) / `limit_hit_blocks()` (layers model scope for the migration target-picker: account-wide always bars, model-scoped bars unless `model_scoped_ok`, the manual ⇆ migrate's leniency); the dashboard serves the computed numbers (effective `usage`, `five_hour_eff`, `limit_hit`) and app.js only reads them, enumerating windows in the served order (docs/relimit.md) |
| The `limit-hit` stamp shape + the limit message's model-scope parse ("You've reached your Fable 5 limit" → `fable`; account-wide → None) | `plugins/claude_code/relimit.py` — the stamp writer and `limit_model()`; the dashboard chip and new-session picker read the stamped `model` field, never re-parse the message (docs/relimit.md *Limit scope*) |
| Audit-import degradation | `core/noaudit.load_audit()` — the ONLY way to get `A`; direct `from core import audit` is reserved for `bin/claude-audit.py` |
| Audit table set | `core/audit._SCHEMA` — derive lists (`prunable_tables()`, `WRITE_COMMANDS`), never hand-copy |
| CSI/OSC escape grammar | the named fragments in `core/render.py` composing `_ANSI`/`_CTRL` |
| Pygments lexer instances (construction compiles token tables; instances are stateless per get_tokens — reusable) | `core/render.lexer(name)` — the one lazy per-process cache; per-call `SomeLexer()`/`get_lexer_by_name` construction is a bug |
| Tailer worst-case caps: per-pump read ceiling + `capped` re-pump contract, opt-in surfaced-line cap + elision marker | `core/tail.py` (`PUMP_MAX_B`/`LINE_MAX_B`); the per-op byte ceiling (`OP_MAX_B`, `verbatim_batches`) is `plugins/claude_code/stream.py`'s |
| Tailer env contract `CLAUDE_STREAM_*` | `hookkit.stream_env()` — launchers pass the raw command, never the render decision |
| Usage dedup + Σ-row arithmetic | `accounting.usage_fold` + `ops.split_tokens` |
| settings.json env-block layering | `model.settings_env` (`nearest_only=` preserves split.py's walk) |
| Context-window occupancy arithmetic (used = fresh + cache-write + cache-read input) + per-model window size | `plugins/claude_code/model.py` — `context_used()`/`context_window()`; the substream's ctx tag/footer and `transcript.context_probe` (the dashboard's ctx chips, `plugins.context()`) are its consumers |
| File-op payload shapes, `FILE_LABEL`/`FILE_RGB` | `plugins/claude_code/tools.py` |
| Claude Code's on-disk task-dir format (`<config>/tasks/session-<first uuid segment>/<id>.json`) + the `tasks` kv snapshot | `plugins/claude_code/task_fmt.py` (`tasks_dir`/`read_tasks`; the dashboard reads the kv, never the dir) |
| Monitor signature-token extraction (the `find_proc` wire contract) | `plugins/claude_code/stream.monitor_sig` |
| Click-to-view stash-and-link | `file_fmt.stash_view` (over the shared `view_ops`) |
| Audit warning-light shapes: the `⚠ N` chip, the `⚠ audit:` mirror ops, `POLL_S`/`FLOOD_N`/`TEXT_MAX`, the `errseen` kv checkpoint | `core/errwatch.py` |
| Cached read-only conns for FIXED-path DBs polled by long-lived processes | `core/tabs.sqc()` (tab DB — all tab-DB reads route through it); `core/errwatch._audit_conn` (audit DB). The per-session STATE DB is deliberately excluded: its reads stay fresh-open (`tabs.sq()` / `state.parked()`'s bare exists check) because file-absence IS the session-alive signal — a cached conn keeps answering from a parked/deleted DB |
| Session-data reads by CONSUMERS (pane renderers, tooling, dashboards) | `core/sessionapi.py` — the one door (presentation-channel delegations + the read model; docs/sessionapi.md). Core internals keep reading `core.state` directly; a consumer importing `core.state` reopens the side door (grep test `test_pane_renderers_read_through_sessionapi`) |
| Claude transcript record shapes (type/user/assistant discrimination, teammate-message unwrap, content-block walk, `result_text`, the `subagents/agent-<id>.*` layout, the `agent-name` naming record — reader AND writer) | `plugins/claude_code/transcript.py` — `parse_line()`/`agent_paths()`/`set_session_title()`; the substream Renderer and `timeline()` are its two presenters (grep tests `test_teammsg_regex_has_one_owner`, `test_agent_name_record_has_one_owner`) |
| Codex rollout record shapes (turn_context/event_msg/response_item grammar, exec-args decode, patch line counts, exit extraction, `usage_split`) | `plugins/codex/rollout.py` — `parse()`/`parse_line()`; the codex stream Renderer and `timeline()` are its two presenters (grep test `test_renderer_consumes_the_parser`) |
| Codex run identity in the read model (`codex_aid` — the streams src_path basename, extension stripped) | `core/sessionapi.py` — `codex_aid()`/`codex_runs()`; the codex activity provider resolves ids only through them |
| stats()/counters→dict shaping | `core/state._stats_from` — shared by `stats()` (live) and `stats_at()` (parked history); a third shaping is drift |
| Paint-op → HTML rendering (SGR/OSC8→spans, `html.escape` as the neutralize analog, the `data-cc` copy/view scheme); conversation-text markdown→HTML (`md_html`, escape-first subset) | `dashboard/opshtml.py` — the WEB presenter of `core/ops.py`'s op vocabulary (the mirror's `_render` is the ANSI presenter; a third op renderer needs a reason) |
| ⧉ copy-text extraction (which ops `cmd`/`out`/`all` collect) | `core/copy.collect` — the terminal click handler AND the dashboard `/copy` endpoint both call it |
| Op producer-source stamp (the `src` field: `sub:`/`team:`/`codex:` vocabulary, the ambient `set_src`/`$CLAUDE_OPS_SRC` mechanics) | `core/ops.py` — `emit()` stamps; producers only declare identity (substream `set_src`, codex `watch.spawn` env, `monitor_fmt`'s explicit `src=`); `dashboard/opshtml.op_items` is the one filter (the web mirror is main-agent-only; the terminal mirror paints everything) |

Adding a new shared fact? Give it one owner in the most-core module whose
charter fits, document the owner here, and (if cheap) add a grep test.

## Module shape

- **No import-time side effects.** The dispatcher imports handler modules on
  hook events (lazily, per selected step — but the always-on `adopt`/
  `tabstatus` set on EVERY event), and tests import modules in isolation — so at import
  time a module must not: read `sys.argv`, resolve a frontend, open/write any
  DB, claim a slot, glob `/tmp`, or do file I/O. Patterns: `_init(argv)`
  called from `entry()` (see `stream.py`/`substream.py`); memoized lazy
  accessors for expensive singletons (`_fe()`/`_win()` in `tabstatus.py`,
  `split.py`). `tests/test_import_safety.py` pins this — extend it when adding
  a module the dispatcher imports.
- **Registries over if/elif ladders.** Type/event switches are data:
  `dispatch._ROUTES`, `tools.RENDER_KINDS`, `Renderer._USE`/`_RESULT`,
  `audit.COMMANDS`, `audit.ANOMALY_SECTIONS`. A new case is one registration,
  and ordering (when load-bearing) is explicit in the table, with a test
  pinning the sequence.
- **Long entry `main()`s are named phases.** The house shape (see
  `stream.py`, `substream.py`, `claude-mirror.py`): small functions named for
  what they do (`wait_source` / `make_pump` / `completion_loop` /
  `emit_footer` / …), a single mutable context object where phases share
  state (`_Loop`), identical control flow. Narrating comments move WITH the
  code they narrate — they document fixed bugs.
- **Lifecycle and rendering are separate concerns** when a streamer grows:
  the lifecycle module owns argv/env, spawning, cancellation, checkpoints;
  the renderer is an import-safe class the lifecycle injects identity and
  hooks into (`substream.py` / `substream_render.py`).

## Errors and the audit

- Hooks must never block or fail; every path exits 0.
- **Every swallow audits first.** `except: pass` without a preceding
  `A.error(...)` is a bug — including partial failures inside a loop (a
  half-done adoption must leave rows saying which half). The only exception:
  the guard *around an audit call itself* (auditing an audit failure is
  circular).
- Get `A` via `load_audit()`. New detached processes go through
  `core.tail.stream_lifecycle` + `core.spawn.spawn_detached` (or its bin/-name
  wrapper `hookkit.spawn_streamer`) — stream rows, spawn rows, and crash audit
  come free. New handlers go through `hookkit.run()`.
- The full audit-coverage checklist (decisions, stream rows, state files,
  transitions, anomaly queries, SKILL.md's schema table AND playbook) is in
  CLAUDE.md § "Every new feature must be audit-covered" — it applies to every
  feature commit, not just new files.

## SQL and databases

- **Bound parameters always** for values. Interpolation is allowed only for
  trusted identifiers (table/column names in migrations/builders) — comment
  such sites.
- Probes on DBs whose *existence* is a signal open `mode=ro` and must never
  create the file (`state.parked()`, `tabs.sq`). Read-modify-write goes
  through `state.immediate()`.

## Magic values and deliberate divergence

- Any literal that is tuning (timeouts, thresholds, poll intervals), protocol
  (wire markers, versions, offsets), or appears twice gets a named constant
  with a one-line comment tying it to the terminal/OS behavior it encodes.
  Env-overridable knobs follow the `CLAUDE_*_S` convention and are listed in
  [testing.md](testing.md) if tests need them.
- When two subsystems *deliberately* differ (per-renderer `CAP_*` values,
  per-vendor price tables, footer denominators), name both sides and mark
  them with a "deliberately different — don't unify" comment. Un-commented
  near-duplicates read as drift and WILL get "fixed" wrong.

## Refactoring discipline

- "Behavior-preserving" means **byte-identical output**. Pin goldens or an
  old-vs-new harness BEFORE moving code, not after. Compare duplicated blocks
  character-by-character before merging them; genuine differences become
  parameters, never casualties.
- Judgment calls (skipping a suggested fix, choosing a different home,
  preserving an oddity) are stated in the commit message.

## Tests

- The suite is hermetic and parallel (`make test` = xdist). Every test runs in
  its own tmpdir via `CLAUDE_MIRROR_TMPDIR`; production code must derive every
  /tmp-ish path from `core/paths.py` so nothing escapes the sandbox.
- **`wait_until` is the one wait primitive** — poll an observable fact
  (an audit row, a DB row, output stability), never sleep blind before a
  positive assertion. A bare `sleep` is legal only to assert the *absence* of
  an event, with a comment saying so. Ceilings scale via `WAIT_SCALE` on CI;
  the pytest-timeout budget must stay above the largest scaled wait (pinned by
  a test — keep the two in lockstep).
- Seed state through real product APIs (`slots.claim`, hook scripts), not
  hand-written SQL — schema changes must break tests loudly.
- Never fixed ports/paths/pids shared across workers; product code gets a
  test env knob (documented in [testing.md](testing.md)) rather than a
  test-only code path.
- Every bug fix ships the test that would have caught it. Refactors extend
  the contract/import-safety/grep tests that guard their rule.

## Linting

- **ruff is the repo's linter** (pinned in `requirements-dev.txt`); the config
  in `ruff.toml` encodes the house rules above — pyflakes + pycodestyle-error +
  pylint-equivalent + bugbear, with every ignore mapped to a documented rule
  (deferred imports = import purity, `global` = the renderer loop state,
  check-less `subprocess.run` = silenced kitten calls, compact one-liners and
  short names allowed, complexity limits off). Don't silence a finding with an
  inline `noqa` when it reflects a house rule — move the rule into `ruff.toml`
  with a comment; `noqa` is for genuine one-off sites (e.g. the mirror's
  pygments availability probe).
- `make lint` must stay clean — CI runs it before the test suite. `make
  lint-fix` applies the safe auto-fixes.

## Docs

- `docs/` is the design record: update the mechanism's doc **in the same
  commit** as a behavior change, including the "why not X" when an
  alternative was considered and rejected.
- CLAUDE.md's module lists, this file's ownership table, and the audit-debug
  skill (schema table AND bug-shape playbook — both) are part of the change,
  not follow-ups.
