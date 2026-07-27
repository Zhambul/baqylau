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
- **"the `plugins` registry root" has a written-down exception list.** Five
  dashboard modules reach a `plugins/claude_code/` module directly, and each is
  deliberate: the web presenter of Claude Code's own tool payloads
  (`opshtml/actclass.py`, `opshtml/tools.py` — the single-owner `FILE_LABEL` /
  file-op shapes, on a per-op render path where a fan-out per lookup would be
  the wrong trade), the memory tab (`read/mirror.py`, `read/session.py` — a
  claude_code feature end to end, whose scope gate this table already assigns to
  `read/session.memory_scope`), and the take-back stash's observation half
  (`http/post/interrupt.py`, likewise already assigned below). The list is
  `DASHBOARD_PLUGIN_REACHES` in `tests/test_l1_contracts.py` and it is enforced
  in both directions — a new reach fails until it is routed through a provider
  or added with a reason, and a row nothing reaches any more fails as stale.
  Before the list the rule above and the ownership rows below simply
  contradicted each other, which is worse than either: nothing told a reader
  which reaches were sanctioned. `PROVIDERS` says WHAT a plugin may be asked;
  this says WHO may ask it directly — the same pair `frontends/` has had all
  along (`test_no_caller_outside_frontends_uses_kitty_internals`).
- Surface shared by two plugins goes in `core/` (that's why `streamfmt.py`
  exists) — never solved by a cross-plugin import or by copy-paste.
- Terminals are reached only through the `Frontend` interface
  (`frontends.get()`). No code outside `frontends/` may touch a kitty-only
  attribute (`.listen`, `.kitten`, `frontends.kitty` internals) — use
  `export_env()` / the interface methods. `tests/test_l0_frontends_contract.py`
  enforces this; keep it passing, don't weaken it.

## Public surface: the underscore means something

- **A leading underscore means "internal to this module's PACKAGE".** Nothing
  outside that directory may import or attribute-reach one. What legitimately
  crosses a MODULE boundary is the inside of one unit: a mixin composed by its
  own package (`_TypingMixin` → `dashboard/http/post/__init__.py`), an
  intra-package helper (`opshtml.ansi._esc`, `read/cache._db_cached`),
  `core/locks.py` borrowing `core/state._connect`. Crossing OUT of the package
  means the name is that module's surface — drop the underscore.
- This had quietly stopped being true. 61 underscore-prefixed names were being
  reached across packages, including the most public function in the whole
  control plane: `launch._frontend`, nine call sites, the single door every
  write handler goes through to reach the terminal. The cost is not style — a
  reader could not tell a module's supported surface from its internals, which
  is the one job the underscore has, and a "private" helper with nine external
  callers is harder to change than a public one because nothing says so.
- Module-level mutable STATE is the deliberate exception in the other
  direction: it stays private and a test that touches it names its owner
  (`DS.presence._VIEWING`), never a facade alias — which would bind the object
  and go stale on a rebind, the same trap as a flat config knob.
- `test_no_private_name_crosses_a_package_boundary` enforces it over the import
  graph (a name-level check, not a grep: the same underscore is right one
  directory away and wrong the next, so only the pair (definer, user) decides).
  Its allowlist is one entry with a stated reason — that is the shape a new
  exception must take.

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
| Reading a NUMERIC `CLAUDE_*` env knob (the missing/empty/unparseable/negative → default rule) | `core/env.py` — `env_float()`/`env_int()`. A raw `float(os.environ.get(X) or D)` is banned: it raises ValueError at IMPORT time, and for `core/tail.py` / `plugins/claude_code/stream.py` that import happens inside a HOOK, before any handler or `A.error` exists — the one spot the hooks-never-fail invariant cannot cover. Two modules had each grown a private guard for exactly this; ~20 sites had none |
| Mirror-pane width default (`DEFAULT_BIAS`, the `CLAUDE_MIRROR_BIAS` fallback both hosts share) | `core/hostpane.py` |
| Claude config dir default (`$CLAUDE_CONFIG_DIR` else `~/.claude`) | `plugins/claude_code/model.config_dir()` |
| Subscription-account vocabulary: the switcher's env contract, `accounts.tsv` registry, per-account config-dir layout (`configs/<slug>`) | `plugins/claude_code/account.py` — `current()`/`registry()`/`alias_for()`/`config_dir_for()` |
| Interactive-login-shell launch wrapper (`$SHELL -lic '<word> "$@"'`, `LAUNCH_SHELLS`) | `plugins/claude_code/account.launch_argv` (via the `plugins.launch_argv` fan-out) — the web launch and the rate-limit migration compose the SAME argv |
| Per-account usage read model: freshest-per-slug `usage`/`limit-hit`/`logged-out` aggregation (the hit AND the logged-out stamp filed under their OWN stamped slug — a migrated session's DB carries the old account's stamp) + what-counts-as-a-window / rolled-over-window / effective-5h / limit-still-active / logged-out-still-active / limit-bars-a-migration-target arithmetic | `core/sessionapi.py` — `account_usage()`/`usage_windows()`/`window_span()`/`_window_rolled()`/`effective_five_hour()`/`effective_usage()`/`limit_hit_active()` (time only — the dashboard pill) / `logged_out_active(stamp, usage)` (ts-vs-freshest-usage, with the `LOGGED_OUT_GRACE_S` margin that keeps the dying session's own post-turn status-line snapshot from self-clearing the stamp: cleared by a re-login's newer snapshot — the dashboard ⚠ badge + the migration target-skip, docs/relimit.md *Logged-out accounts*, *Why the grace margin*) / `model_available(hit, model)` (per-model bar for the migration ladder: account-wide bars every model, model-scoped bars only its own family — docs/relimit.md *Model-downgrade ladder*); the dashboard serves the computed numbers (effective `usage`, `five_hour_eff`, `limit_hit`, `logged_out`) and app.js only reads them, enumerating windows in the served order (docs/relimit.md) |
| New-session default-account scheduling arithmetic: weekly-quota PERISHABILITY (`remaining% / hours-to-7d-reset`, objective (b) — burn quota that resets soonest first) + the 5h session-safety gate | `core/sessionapi.py` — `sched_score()`/`sched_ok()` + the `SCHED_5H_GATE`/`SCHED_MIN_HORIZON_H` knobs; `accounts_payload` serves `sched_score`/`sched_ok` per account and app.js `autoAcct` only reads them (`schedScore`), never re-derives (docs/dashboard.md *Default account*). Distinct from the migration target picker (`account.pick_target`, least-used-5h) — deliberately NOT unified: that runs on tokenless snapshots with no 7d reset and is the safety net, not the scheduler |
| The `limit-hit` stamp shape + the limit message's model-scope parse ("You've reached your Fable 5 limit" → `fable`; account-wide → None) + the message's reset-time parse ("resets 2:40am (Asia/Makassar)" → epoch) + the `logged-out` stamp shape (`{slug, ts, msg}`, StopFailure `error='authentication_failed'` — a revoked/expired login) | `plugins/claude_code/relimit.py` — the stamp writer, `limit_model()`, and `limit_reset()`; the dashboard chip and new-session picker read the stamped `model`/`resets_at`/`logged-out` fields, never re-parse the message (docs/relimit.md *Stamp `limit-hit`*, *Limit scope*, *Logged-out accounts*) |
| Model FAMILY word of a model id/alias + the rate-limit downgrade order (`fable`→`opus`→`sonnet`, Sonnet floor) | `plugins/claude_code/model.py` — `family()`, `MODEL_LADDER`, `ladder_from()`; the migration ladder and relimit's model resolution are the consumers (docs/relimit.md *Model-downgrade ladder*) |
| Migration target selection — walk the model ladder from the current model, rank each rung by most headroom, pick the best account + model (or the keep-model fallback when the model is unknown) | `plugins/claude_code/account.py` — `pick_target(cur_slug, cur_model, …)`; both the automatic rate-limit path and the manual ⇆ (via `plugins.migration_target`) call it, differing only in the % `ceiling` (docs/relimit.md *Model-downgrade ladder*) |
| Per-MODEL weekly usage: the OAuth `/usage` fetch + keychain-login piggyback + refresh ownership + account→slug reset-match + `weekly_scoped`→`seven_day_<model>` shaping | `plugins/claude_code/model_usage.py` (via the `plugins.model_windows` fan-out) — the ONE token'd usage source; the dashboard merges its windows into `account_usage`'s tokenless snapshot, core/hooks never call it (docs/dashboard.md *Per-model usage bars*) |
| The PLUGIN provider surface — which optional functions a plugin may expose and the arity each fan-out calls them with | `plugins/__init__.py` — `PROVIDERS` + `provider()`, the one door every fan-out goes through (a bare `getattr(p, …)` is banned, grep-tested). The frontends' `Frontend` base class is the same rule for terminals; plugins had duck typing and no contract, so a misspelled or arity-drifted provider was never found and the feature degraded silently (tests/test_l1_contracts.py) |
| Audit-import degradation | `core/noaudit.load_audit()` — the ONLY way to get `A`; direct `from core import audit` is reserved for the CLI tier (`core/auditcli.py` and its entry `bin/claude-audit.py`), which is a layer over the audit module rather than a consumer of it |
| Audit table set | `core/audit._SCHEMA` — derive lists (`prunable_tables()`, and `core/auditcli.WRITE_COMMANDS` from its `COMMANDS` table), never hand-copy |
| The audit CLI's command table + the canned anomaly queries + the row formatters | `core/auditcli.py` — the READ/REPORT tier; it imports `core/audit.py` (via the public `A.connect()`) and the direction must never invert, or 638 lines of reporting land back on the per-event path that every hook pays (test `test_the_audit_write_path_never_imports_its_report_tier`) |
| CSI/OSC escape grammar | the named fragments in `core/render.py` composing `ANSI_RE`/`_CTRL` |
| Pygments lexer instances (construction compiles token tables; instances are stateless per get_tokens — reusable) | `core/render.lexer(name)` — the one lazy per-process cache; per-call `SomeLexer()`/`get_lexer_by_name` construction is a bug |
| File extension → lexer name (which files are "source" at all: the `code` render kind, the file-op expand, the dashboard presenter) | `core/coderender.LANGS` — one row per EXTENSION, and no key may be a suffix of another: two consumers match with `endswith` (`tools._lexer_match`, `opshtml.tools._lexer_for`), so `.jsx`/`.cjs`/`.tsx` riding on a `.js`/`.ts` row would let dict order pick the lexer (pinned by `test_code_langs_keys_are_suffix_unique`) |
| The bounded BACKWARDS read of a big JSONL file (last N bytes as complete lines) + the drop-the-torn-first-line rule | `core/tail.tail_lines` — FileTailer's discipline from the other end; the four read-side probes that each re-encoded it (`transcript._title_records`/`context_probe`/`goal_probe`, `model.session_model`) go through it, and the WINDOW SIZE stays per-probe (`TITLE_TAIL_B`/`CTX_TAIL_B`/`TAIL_SCAN_BYTES` — deliberately different, each tied to how far back its record can sit) |
| Tailer worst-case caps: per-pump read ceiling + `capped` re-pump contract, opt-in surfaced-line cap + elision marker | `core/tail.py` (`PUMP_MAX_B`/`LINE_MAX_B`); the per-op byte ceiling (`OP_MAX_B`, `verbatim_batches`) is `plugins/claude_code/stream.py`'s |
| Tailer env contract `CLAUDE_STREAM_*` | `hookkit.stream_env()` — launchers pass the raw command, never the render decision |
| Usage dedup + Σ-row arithmetic | `accounting.usage_fold` + `ops.split_tokens` |
| settings.json env-block layering | `model.settings_env` (`nearest_only=` preserves split.py's walk) |
| Context-window occupancy arithmetic (used = fresh + cache-write + cache-read input) + per-model window size | `plugins/claude_code/model.py` — `context_used()`/`context_window()`; the substream's ctx tag/footer and `transcript.context_probe` (the dashboard's ctx chips, `plugins.context()`) are its consumers |
| File-op payload shapes, `FILE_LABEL`/`FILE_RGB` | `plugins/claude_code/tools.py` |
| A block header's WEB-FACING wording (the op's `note`: `⏺ Agent "…" launched / finished · 21m 31s`, `⏺ Teammate @fix-smoke-dedup finished`, `⏺ Message team-lead → rev-ui-util: …`, `⏺ Mail team-lead → rev-ui-util · read`) | `core/ops.py` owns the FIELD (`label(note=)`, threaded by `core/streamfmt.chip`); the WORDING is the producer's — `substream_render.agent_note` for the two subagent blocks and `msgs.note_message`/`note_mail` for team mail, one builder per pair so the two halves cannot drift. The presenter renders it and never re-words a chip: parsing a chip apart to reword it is the sniffing `actclass` ended (its `legacy_note` fallback is for chips ALREADY ON DISK only) |
| The two REGISTERS an agent is named in — `Agent "<type>"` for a Task-spawned subagent, `Teammate @<name>` for an agent-team member (Claude Code's own words) | `core/streamfmt.py` — `AGENT_WORD`/`TEAM_WORD` + `agent_note`; in core because both a producer (`substream_render.agent_note`, which knows it from its `team` palette) and the web presenter (`actclass.legacy_agent_note`, which reads the op's `src` stamp) must word it the same, and a dashboard module may not reach into a plugin for a string |
| The SUBJECT id several ops about one thing share (the op's `mid`), for counting distinct subjects in a collapsed run | `core/ops.py` owns the FIELD (`label(mid=)`/`gut(mid=)`); today's only subject is a team message's msg_id (`plugins/claude_code/msgs.py` stamps arrival + body + read notice with it). The page's `VIEW_SUBJECT` table (`app.05-session.js`) is the read side, and holds the agent case too (keyed on `src` rather than `mid`) — the counters that count SUBJECTS instead of rows live in exactly one table |
| The `⇢ prompt` / `⇠ result` block MARKERS | `core/streamfmt.py` — `MARK_PROMPT`/`MARK_RESULT`; `substream_render` builds the chips from them and `opshtml/actclass.legacy_agent_note` reads them back for ops written before `note` existed (parked ops cannot be re-stamped) |
| Claude Code's injected `<system-reminder>` blocks (stripping them out of agent-facing text) | `plugins/claude_code/transcript.py` — `strip_reminders`; it is a fact about Claude Code's transcript text, so the strip lives with the record shapes and every surface inherits it |
| Claude Code's on-disk task-dir format (`<config>/tasks/session-<first uuid segment>/<id>.json`) + the `tasks` kv snapshot | `plugins/claude_code/task_fmt.py` (`tasks_dir`/`read_tasks`; the dashboard reads the kv, never the dir) |
| The task line's GLYPHS (`✚` created / `✓` completed) | `plugins/claude_code/task_fmt.py` — `GLYPH_NEW`/`GLYPH_DONE`/`GLYPHS`; the web classifier (`opshtml/actclass.py`) IMPORTS them to recover the `task` activity class, because a paint op carries no "this is a task row" field |
| Agent-team MAIL in the mirror: the `✉` sent / `●` delivered / `◉ read · ` glyphs, their yellow/green colours, both wordings (`Message …` for the row that CARRIES a message, `Mail … · delivered/read/idle` for the poller reporting on one), the body cap, Claude Code's teammate LIFECYCLE-FRAME vocabulary (`idle_notification`/`task_assignment`/… — the JSON its inbox records carry instead of prose), and the ops themselves | `plugins/claude_code/msgs.py` — `GLYPH_SENT`/`GLYPH_NEW`/`GLYPH_READ`/`READ_PREFIX`/`MSG_*_RGB`/`note_message`/`note_mail`/`CAP_TEXT`/`FRAME_PHRASE`/`FRAME_TEXT`/`frame`/`frame_words` (one builder words a frame for BOTH surfaces, so the pane's chip and the web's note cannot disagree about the same frame) + `sent_ops(...)` (the MESSAGE block, emitted by `mail_fmt.py` at send time — chip and text share a copy-group) and `event_ops(events, log)` (the poller's one-line transitions). Both stamp the message's `msg_id` as the op's `mid`. The plugin's `census()` returns the poller's ops, so the tool-agnostic `claude-scorebar.py` only emits what it is handed (it may not import a plugin's internals, and an entry script cannot be imported by the classifier that reads the glyphs back). Read side: `opshtml/actclass.mail_pair` is the ONE parser of those chips (wording fallback, legacy subject key, plumbing flag), and `mail_plumbing` the one place the "message vs mail system" rule lives |
| Memory-wiki vocabulary: the root path (`~/wiki/01`), the project SCOPE (`~/code/01/aggregator-adapters`), the memory-op test, the project gate, the mirror ❖ `MARK`, the `memory` kv snapshot (write side), and the vault link-resolve/backlink/read helpers | `plugins/claude_code/memory.py` (`root()`/`project()`/`is_memory`/`in_scope`/`MARK`/`record`/`resolve`/`backlinks`/`read_note`; producers gate `is_memory(path) and in_scope(cwd)`, the dashboard serves `memory_scope` + reads the kv + renders notes via `dashboard/notehtml.py`, docs/dashboard.md *Memory tab*; the dashboard-side APPLICATION of that gate is `dashboard/read/session.memory_scope`/`memory_count` — its two readers, the overview payload and the SSE badge table, must not each re-apply it) |
| Pending modal-dialog kv keys (`ask-pending`/`plan-pending` stash + the `ask-draft` clear boundary) | `plugins/claude_code/ask_fmt.py` (`KEY`/`PLAN_KEY`/`DRAFT_KEY`; the dashboard WRITES `ask-draft` via `post_ask_draft`, but ask_fmt owns when it clears — same boundary as `ask-pending`) |
| Monitor signature-token extraction (the `find_proc` wire contract) | `plugins/claude_code/stream.monitor_sig` |
| Click-to-view stash-and-link | `file_fmt.stash_view` (over the shared `view_ops`) — file ops; a code-reading command's Read one-liner has its OWN command+output stash builder `cmd_fmt._stash_read_view` (same `view:<gid>` protocol, different block: a `code` op + a lex `gut` op, header carrying ⧉cmd/⧉out), deliberately NOT shared |
| Render-as-Read decision for a code-reading Bash command (which sed/grep/cat/head/tail-of-source collapses to a Read one-liner instead of streaming) | `plugins/claude_code/tools.py` — `read_command`/`code_read_target` over the shared `_match_reader` (which also backs `_detect_source`/`code_source`, so the file/reader match is encoded once); both Bash hooks gate on `read_command` (`cmd_pre` skips streaming, `cmd_fmt._render_read` renders), `CLAUDE_MIRROR_CMD_READ` toggles it |
| Audit warning-light shapes: the `⚠ N` chip, the `⚠ audit:` mirror ops, `POLL_S`/`FLOOD_N`/`TEXT_MAX`, the `errseen` kv checkpoint | `core/errwatch.py` |
| Web text-presentation (the NO-EMOJI rule): the emoji-capable codepoint set + the U+FE0E pass that pins it monochrome | `dashboard/opshtml/ansi.py` — `text_presentation()`, applied at the `_esc` escape leaf so every op/chip/markdown path takes it; `tp()` in `dashboard/static/app.00-core.js` (inside `el()`/`tnode()`) is its deliberate twin for glyphs the PAGE writes, since JS can't import it. It lives in the presenter, never the producers — the glyphs are audited terminal vocabulary (docs/dashboard.md *No emoji*) |
| Cached read-only conns for FIXED-path DBs polled by long-lived processes | `core/tabs.sqc()` (tab DB — all tab-DB reads route through it); `core/errwatch._audit_conn` (audit DB). The per-session STATE DB is deliberately excluded: its reads stay fresh-open (`tabs.sq()` / `state.parked()`'s bare exists check) because file-absence IS the session-alive signal — a cached conn keeps answering from a parked/deleted DB |
| Live→parked demotion check (a state-DB-live session whose kitty window is gone — the 4-condition `live`/`live_wins is not None`/`kitty_window_id`/`sid not in live_wins`/past-grace test + the `live=False` mutation) | `dashboard/control/launch.demote_if_dead` — the list / session-detail / resume read payloads all call it; the session detail passes a separate `target` because its liveness comes from `API.session` while the window comes from the audit `session_row` (docs/dashboard.md *Liveness = an OPEN tab*) |
| Session-data reads by CONSUMERS (pane renderers, tooling, dashboards) | `core/sessionapi.py` — the one door (presentation-channel delegations + the read model; docs/sessionapi.md). Core internals keep reading `core.state` directly; a consumer importing `core.state` reopens the side door (grep test `test_pane_renderers_read_through_sessionapi`) |
| Process-lifetime path-keyed memo + its bound (the `(path, db_sig)` state-DB read memo AND the LRU that caps any such per-session/transcript/cwd cache in a long-lived singleton) | `core/sessionapi.py` — `db_sig()`/`db_cached()`/`BoundedLRU`; the dashboard's seven memos (`_TITLES`/`_CTX`/`_GIT`/`_DIRTY`/`_STATS`/`_ACCT`/`_CMDS`) are `BoundedLRU(MEMO_CAP)` so a days-long server can't grow one entry per session ever seen — freshness (size/sig/TTL) fixes stale VALUES, the LRU bounds the KEY set; every value is re-derivable so eviction just re-reads once. The same bound covers the two in-memory PRESENCE maps, which have the same key-set shape without being memos: `dashboard/notify/presence.py`'s `_VIEWING` is swept EXACTLY (an entry past its beat deadline is dead by definition, so `mark_viewing` reaps them — no live entry can be dropped) and `_DEVICE_SEEN` is a `BoundedLRU(DEVICE_SEEN_CAP)` (nothing there ever goes stale, so the LRU drops the least-recently-beaten device, which cannot be the MRU target) |
| A control-plane POST's audit target — the `(row, log, sdb)` triple every session-scoped handler files its `state_files`/`errors` rows under, incl. BOTH fallbacks for a sid with no audit row | `dashboard/http/base._Base._audit_target` — 11 handlers were re-encoding it in four spellings, one of which (`P.mirror_log("")` for a log-less upload) silently resolved to the dashboard process's own CWD SLUG and filed rows in an unrelated session's timeline. `get_copy`/`get_view` keep their own two lines on purpose (they need the STRICT `state_db_for`, since they branch on its absence). Session-scoped INPUT REJECTS reach it via `_reject_input(..., sid=sid)` rather than a hand-passed `log=`, so a handler's reject row and its success row can't diverge for a forked sid |
| The per-session state DB's kv WIRE FORM (the upsert statement + the `ensure_ascii=False` JSON encoding) and its decode | `core/state.py` — `_KV_UPSERT`/`_kv_val()`/`_kv_read()`; the three writers (`kv_set`, `kv_set_at`, `kv_cas_seq_at`) and the two readers used to spell both out. `dashboard/prefs.py` has the byte-identical statement in its own `_upsert` and is DELIBERATELY not sharing this one — a different DB with a different lifecycle, matching by convention rather than contract |
| The open / guard-None / try / except-default / finally-close frame around a path-keyed state-DB read or write | `core/state.py` — `_read_at`/`_write_at` over `_ro`/`_rw`; nine callers were nine copies of it. A reader is now its query plus its miss value |
| Read-only per-session kv read (`state_db_for` + mode=ro `kv_at`, None when the session has no state DB) | `dashboard/read/meta.session_kv` — the modal-dialog stashes, the composer draft/queue, the tasks snapshot, the click-to-view stash and the account slug all read through it; never a fresh `state_db_for`+`kv_at` pair at the call site (it can never CREATE the DB whose existence is a liveness signal) |
| Claude transcript record shapes (type/user/assistant discrimination, teammate-message unwrap, content-block walk, `result_text`, the `subagents/agent-<id>.*` layout, the `agent-name` naming record — reader AND writer) | `plugins/claude_code/transcript.py` — `parse_line()`/`agent_paths()`/`set_session_title()`; the substream Renderer and `timeline()` are its two presenters (grep tests `test_teammsg_regex_has_one_owner`, `test_agent_name_record_has_one_owner`). The record-kind VOCABULARY is `KINDS`, and the module's own two presenters dispatch on it as REGISTRIES — `_CONV` (+ the declared `_CONV_SKIP` drops) for the dashboard's message stream, `_FOLD` (total) for the drill-down timeline — checked against `KINDS`, and `KINDS` against `parse_line` itself, by `test_both_transcript_presenters_cover_every_record_kind` / `test_declared_kinds_are_the_kinds_parse_line_actually_emits`. They were parallel elif ladders, so a new kind silently reached one presenter and not the other |
| The transcript's message TREE — `uuid`/`parentUuid`, and which branches Claude Code DISCARDED (a cancelled or rewound-away prompt is re-parented around, never deleted) | `plugins/claude_code/transcript.py` — `_line_meta()`/`_prompt_bearing()`/`_dead_uuids()`; `conversation()` prunes with them and echoes `par` so the page can prune live (docs/dashboard.md, *Discarded prompts*) |
| The take-back stash (`takeback` kv: uuids an observer saw Claude Code hand back to the input box, before the transcript can show it) | `plugins/claude_code/transcript.py` — `taken_back()`/`mark_taken_back()` (both halves); the dashboard's `post_interrupt` supplies the observation, `conversation_for` feeds it back as `suspects` |
| "The TUI input box holds text the web put there" (`tui-draft` kv — what makes the next send REPLACE rather than append) | `dashboard/control/launch.py` — `tui_draft()`/`set_tui_draft()`; set by the interrupt's take-back and by a rewind restore, consumed by `post_message` |
| The `composer-draft` kv's WRITERS (a page's debounced save, and the terminal→web sync) | `post_composer_draft` + `dashboard/control/launch.sync_terminal_draft()` — both go through `state.kv_cas_seq_at` (seq-guarded CAS, never a bare write) and stamp an `origin`; a third writer that skips either is drift |
| Per-session kv WRITES from the dashboard (a ThreadingHTTPServer: every request is its own thread) | `core/state.kv_set_at()` — a fresh connection per call. `kv_set()`'s cached connection is bound to the thread that opened it and from any other thread writes NOTHING while returning False (grep test `test_no_dashboard_code_calls_the_thread_bound_kv_set`); its bool must always be checked |
| Slash-command delivery into a live TUI (a BRACKETED PASTE + the clipboard-image guard — raw keystrokes are vim COMMANDS in a NORMAL-mode input box) | `dashboard/control/launch.type_command()` — `post_rewind`/`post_command`/`rewindmenu.drive` all go through it; nothing may `send_text` a `/…` command (grep test `test_no_slash_command_is_send_text_anywhere`, plus the end-to-end `test_slash_commands_never_reach_the_tui_as_keystrokes`) |
| Codex rollout record shapes (turn_context/event_msg/response_item grammar, exec-args decode, patch line counts, exit extraction, `usage_split`) | `plugins/codex/rollout.py` — `parse()`/`parse_line()`; the codex stream Renderer and `timeline()` are its two presenters (grep test `test_renderer_consumes_the_parser`) |
| Codex run identity in the read model (`codex_aid` — the streams src_path basename, extension stripped) | `core/sessionapi.py` — `codex_aid()`/`codex_runs()`; the codex activity provider resolves ids only through them |
| stats()/counters→dict shaping | `core/state._stats_from` — shared by `stats()` (live) and `stats_at()` (parked history); a third shaping is drift |
| A stream item's ACTIVITY CLASS — which kind of activity a paint op represents (`act`: bash/bg/monitor/read/edit/write/agent/task/mail/warn/msg), whether it reports a failed outcome (`bad`), and a mutation one-liner's `+A -R` counts | `dashboard/opshtml/actclass.py` — `classify()`/`diffstat()`/`ACTS`; `op_items` stamps every item with it and the page only READS it. It is also the ONE reader of the mirror's block-opening glyph vocabulary (`▶ ▷ ◉ ↻ ■`, producer-emitted) — the page's own `CMD_GLYPH` regex over rendered chip text is gone. Keys on structure before text: the semantic colours are IMPORTED from `core/ops.py` (a `RED` chip IS a failure; a `▶` in a slot-PALETTE colour is a subagent launch, not a command) and the file verbs from `plugins/claude_code/tools.FILE_LABEL`, the task glyphs from `plugins/claude_code/task_fmt.GLYPHS` and team mail's glyphs+colours from `plugins/claude_code/msgs` (all three IMPORTED — respelling `◉` here is what had a mail-read notice classified as a MONITOR, and counted as one). A GROUP-LESS body op (`gut`/`code` with no `g`) inherits the class of the item it follows — `op_items`, not `classify()`, since it is the neighbour that is its block; it works only within ONE `op_items` call, which is why `read/mirror.py` batches consecutive ops (a per-op call has no row in front of it) and why a two-op block is GROUPED at the source instead. It inherits that neighbour's SUBJECT (`mid`) and its PLACEMENT too — `op_items` inserts it at the header's index, because the page reverses the item list for its newest-top feed and an appended body therefore renders ABOVE the row it belongs to (mail's `<from> → <to>` pair, parsed once by `actclass.mail_pair`, plus the arrival op's ROW ID is the subject key for history with no `mid` — hence `op_items(ops, key, ids)`). Deliberately NOT stamped by the producers the way `src`/`web` are: the class is recoverable from the op, so stamping would mean eight formatters plus this classifier anyway (parked history can't be re-stamped) — two implementations that drift (docs/dashboard.md *View modes*) |
| The collapsed-run SUMMARY vocabulary (Claude Code's own, read out of its binary: per-counter verb pairs + units, the fragment ORDER, the capitalize-the-first / join-with-", " / participle-plus-"…"-while-running rules) | `VIEW_FRAGMENTS` in `dashboard/static/app.05-session.js` — a deliberate JS-only owner, because the counts are computed over the assembled client feed (see docs/dashboard.md *View modes* for why the grouping cannot be server-side). Its keys must stay `actclass.ACTS` tokens: grep-tested both ways by `test_act_vocabulary_matches_the_page_phrase_table`, and the wording itself is executed by `tests/jsdom/viewmode.js` under node |
| Paint-op → HTML rendering (SGR/OSC8→spans, `html.escape` as the neutralize analog, the `data-cc` copy/view scheme); conversation-text markdown→HTML (`md_html`, escape-first subset) | `dashboard/opshtml/` (`ansi`/`ops`/`markdown`/`tools`) — the WEB presenter of `core/ops.py`'s op vocabulary (the mirror's `_render` is the ANSI presenter; a third op renderer needs a reason) |
| ⧉ copy-text extraction (which ops `cmd`/`out`/`all` collect) | `core/copy.collect` — the terminal click handler AND the dashboard `/copy` endpoint both call it; when the group has NO ops-table ops (a collapsed code-read block, whose command/output live only in the `view:<gid>` stash) it falls back to that stash |
| Op producer-source stamp (the `src` field: `sub:`/`team:`/`codex:` vocabulary, the ambient `set_src`/`$CLAUDE_OPS_SRC` mechanics) | `core/ops.py` — `emit()` stamps; producers only declare identity (substream `set_src`, codex `watch.spawn` env, `monitor_fmt`'s explicit `src=`); `dashboard/opshtml.op_items` is the one filter (the web mirror is main-agent-only; the terminal mirror paints everything) |
| Unsent-composer draft kv (`composer-draft` write/clear boundary — write on edit, delete on send/empty; NO plugin lifecycle, unlike `ask-draft`) | `dashboard/http/post/state.py` — `post_composer_draft` writes, `dashboard/read/session.composer_draft` reads; a message draft has no turn boundary so the dashboard fully owns it |
| Delivered-prompt match rule — does a transcript prompt carry what the web composer sent (the ⧗ queued chips' AND the optimistic bubbles' reconcile key)? A SUFFIX match: the delivery can carry attachment `@path` mentions OR a terminal-restored draft glued in front of it | `dashboard/read/session.chip_delivered` — its deliberate twin is `promptMatches` in `dashboard/static/app.00-core.js` (JS can't import it), through which BOTH client reconcilers go (`drainQueue`, `drainPending`). Three hand-rolled copies drifted apart once, pinning a chip forever (grep test `test_app_js_drains_through_the_shared_prompt_match`, docs/dashboard.md *Web composer queue*) |
| Which slash-command NAMES are real for a cwd, and what a message's leading `/command` token is — the truth behind the `/` menu's list AND the `/command` tint in both the input boxes and the prompt bubbles | `plugins.slash_commands(cwd)` discovers them (`plugins/claude_code/slashcmds.py`); `dashboard/read/meta.py` `cmd_names(cwd)`/`session_cmds(sid)` is the TTL'd name-set projection every tint reader goes through, and `session_payload` ships it to the page as `commands` rather than letting the page discover its own. The leading-token rule has exactly two implementations, marked as twins: `opshtml/tools.py` `_lead_cmd` (the transcript bubbles the server renders) and `app.06-clientlog.js` `leadCmd` (the optimistic/queued bubbles the page builds itself); the input overlay's own copy lives in `app.05-session.js` `cmdHighlight`. The tint HUE is the CSS custom property `--cmdtint` (docs/dashboard.md *The "/" menu*) |
| Which PROJECT DIRECTORY a session belongs to — the list page's group key AND the new-session directory picker's suggestion list (a linked-worktree cwd resolves to its owning main checkout, so `.claude/worktrees/<name>/` never shows up as a place to start a session) | `dashboard/read/meta.group_dir` computes it server-side (frozen `start_cwd` → worktree owner) and `sessions_payload` serves it as `row.group_dir`; `groupKey(row)` in `dashboard/static/app.00-core.js` is the ONE client-side reader (`group_dir || cwd || ""` — the `cwd` fallback is for rows pushed by a not-yet-restarted server), used by BOTH `groupSessions` and `openNewSession`'s `suggest()` list. Two inline copies of the fallback expression is how the picker came to offer worktree dirs the list had already folded away. WHICH of those directories the picker OFFERS is one step further and picker-only — `nsSuggestDirs`/`NS_SCRATCH` in `app.09-newsession.js` (groupKey + drop any path containing `/tmp`); the list deliberately does NOT filter, a scratch session still needs its card (docs/dashboard.md *Grouping and titles*) |
| The secondary list TABS' machinery (monitors / jobs / memory: fetch, list cache, card grid, live poll, tab badge, drill-down + breadcrumb) | `SECTIONS` in `dashboard/static/app.11-chrome.js` — one descriptor row per tab, everything else generic over it (`loadSection`/`renderSectionGrid`/`scheduleSectionPoll`/`clearSectionPoll`/`showSection`/`repaintSectionDetail`/`sectionCrumbs`/`setSectionCount`/`updateSectionCount`). Written twice before — fourteen near-identical function pairs, `sortedMonitors`/`sortedJobs` byte-identical but for a parameter name. Executed by `tests/jsdom/sections.js`, not grepped |
| The PAGE's own repeated shapes — the Σ token-breakdown chip (`sigmaChip`, the twin of `core.ops.token_parts`: the session scoreboard, the drilled-in agent scoreboard and the agent timeline header all show it, and the server hands them the four counters under two different field spellings, so the caller maps fields and the owner does the arithmetic + wording), the scoreboard chip / meta-grid appenders (`chipAdder`/`metaAdder`), the ctx-saturation row repaint (`paintCtxRow`), the tab count badge + its cached `meta` counterpart (`setTabBadge`) | `dashboard/static/app.00-core.js` (+ `app.11-chrome.js` for the two that touch `S.ses` chrome) — JS can't import the Python owners, so these are deliberate twins; a re-encoded copy drifts on the one tweak that only reaches one of them |
| The server-side numbers the PAGE must act on before any request reaches a handler — the upload cap it refuses an over-size attachment against, the rename input's `maxLength`, and the presence TTL its heartbeat cadence is DERIVED from | `dashboard/config.py` (`UPLOAD_MAX`/`RENAME_MAX`) + `dashboard/notify/presence.py` (`VIEW_TTL_S`) own them; `GET /api/limits` is the one channel and `LIMITS` (`dashboard/static/app.00-core.js`) the one place the page keeps them — its literals are the PRE-FETCH fallback only. Each was a JS literal with a `mirrors the server's X` comment; `VIEW_TTL_S` is env-overridable, so that copy drifted with no code change at all and fired off-device alerts at a session you were watching (docs/dashboard.md *Served limits*, grep test `test_page_reads_the_served_limits_not_its_own_copies`) |
| An optimistic close's in-flight state — the PAIR `S.closing` (greyed card, disabled ✕) + `S.closePend` (the `optPending` web-hint handle), which must move together and settle EXACTLY once (a leaked handle beacons a bogus `web-hint stale` row 20s later, manufacturing the stuck-state signal the beacon exists to report) | `dashboard/static/app.00-core.js` — `closeBegin`/`closeSettle`; three sites in two files hand-rolled the pairing (card ✕, header ✕, `reconcileCloses`), every other site only READS the maps (grep test `test_close_in_flight_state_has_one_owner`) |
| "Is a turn REALLY running in this window" — the marker-free screen-DELTA liveness probe (two ANSI-stripped captures a beat apart; no marker string survives Claude Code's versions) | `dashboard/http/post/interrupt.py` — `_screen()` is the capture unit, `_turn_live()` the two-capture verdict for `post_message`'s `queued` promise; `_escape_press`'s interrupt loop keeps its OWN capture pair because it re-presses BETWEEN them (deliberately not unified). The TAB COLOUR is never a substitute: Claude Code fires no hook on cancel (docs/dashboard.md *The tab colour alone cannot promise `queued`*) |
| Input-box ghost-suggestion parse — extracting Claude Code's greyish "suggested answer" from an ANSI screen capture (the faint-SGR `\x1b[22;2m` input line between the grey divider rules) | `dashboard/suggestion.py` — `parse()` (pure, unit-tested) + `probe()` (audited get-text); sibling of `askdialog.py`/`plandialog.py`, the same live-TUI-pixels-are-the-only-source philosophy (docs/dashboard.md *Web ghost suggestion*) |
| The GENERIC plumbing shared by the screen-verified dialog drivers (`askdialog`/`plandialog`/`confirmdialog`/`rewindmenu`): the screen re-read poll loop + the step-error base | `dashboard/screendrive.py` — `poll_until(fe, win, pred, timeout, sleep, poll)` returns `(screen, held)`; `StepError(step, detail, screen)` base (the four keep DISTINCT names — post.py catches each by name). ONLY these two are shared; each driver's dialog ANATOMY (keys, region parsing, bail semantics) stays deliberately per-driver, NOT unified (docs/dashboard.md) |
| What the per-session SSE stream PUSHES on change — one row per field (`prev`-map key, event name, producer over the per-tick `_Tick`, wire wrapper), its cadence (slow/fast), and the per-connection last-sent map DERIVED from those tables plus `_INLINE_KEYS` | `dashboard/http/sse.py` — `_SLOW_CHANS`/`_FAST_CHANS`/`_INLINE_KEYS`/`_prev_map()`; the four tab-badge counts keep their own `_BADGE_COUNTS` table inside it. The `prev` literal that used to sit beside the stanzas had already lost `view_mode`, and the flat 200-line loop scope it lived in is what let the badge stanza's `for key, …` clobber the session's mirror-log key and the tick counter (docs/dashboard.md *The stream's pushed fields are a channel table*) |
| GLOBAL (cross-session/cross-device) dashboard preferences store — the new-session form's `{cwd, model, effort}` AND its unsent first-prompt drafts, one PER DIRECTORY `{cwd: {text, seq}}` (`NS_DRAFT_KEY`/`NS_DRAFT_MAX`/`ns_drafts()`/`ns_draft()`/`set_ns_draft()` — the `composer-draft` analog for the box that has no session yet; the cwd KEY's normalization is owned by the page, `app.09-newsession.js` `nsDirKey`, and stored verbatim here so there is one implementation, not two), the hidden-directories set `{group_key: hidden_at}` (`HIDDEN_KEY`/`hidden_dirs()`/`hide_dir()`), the notify-mute + web-rename maps, AND the Web Push state: the per-device subscriptions `push-subs` (`push_subscriptions()`/`add_`/`remove_push_subscription()`) + the stable VAPID keypair `vapid-keypair` (owned by `dashboard/webpush.py`), AND each session's mirror VIEW MODE `view-mode` `{sid: mode}` (`VIEW_MODES`/`VIEW_DEFAULT`/`view_mode()`/`set_view_mode()` — the wire vocabulary the endpoint validates against and the page's segmented control renders from; deliberately per-SESSION with no global default; `VIEW_MODES` is in CONTROL order while `VIEW_DEFAULT` is `default` — the two are decoupled, so neither side may take the first mode for the default — and a session set back to the default is stored as an ABSENCE) | `dashboard/prefs.py` (kv table at `core.paths.DASH_PREFS_DB`, ~/.claude, durable) — the one dashboard store that is NOT per-session and CREATES its DB on demand (no session-alive meaning); callers are `GET`/`POST /api/ns-prefs`, `GET`/`POST /api/ns-draft`, `GET /api/dirs/hidden` + `POST /api/dirs/hide`, and `GET /api/push/config` + `POST /api/push/subscribe`/`unsubscribe`. The hidden-dir RE-APPEAR predicate (a session with `started_at > hidden_at` un-hides) lives client-side in `app.js` `dirHidden`, not here (docs/dashboard.md *Hidden directories*, *Web push*) |

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
- **A function-level import needs one of four reasons, named at the site.**
  Top-level is the default. `ruff.toml` ignores `PLC0415` wholesale because the
  purity rule above produces deferred imports legitimately — but the ignore
  makes a copy-pasted one indistinguishable from a deliberate one (a pylint pass
  found four re-imports of modules the same file already imports at the top). So
  a deferred import says which reason put it there: **(1) measured cost on a
  per-event path** — `python3 -X importtime`, and the bar is real: a bare
  interpreter is ~24ms, the whole hook dispatcher's graph is ~11ms on top, and
  only `core/mdrender.py` → `wenmode` (~40ms) is worth deferring at all;
  **(2) a cycle break**; **(3) registry fan-out**, where the point is that most
  events need none of the providers (`plugins/claude_code/__init__.py`);
  **(4) an import with unavoidable side effects** — rare, and prefer fixing the
  callee with a lazy accessor. Note an *optional dependency* is NOT on that
  list: it belongs at the top under `try/except ImportError` with a degrade path
  (`core/mdrender.py`, `wenmode`). The cost reason gets a test, not trust —
  `test_per_event_imports_stay_off_the_heavy_renderers` asserts the per-event
  modules' import graph contains no `wenmode`/`pygments`/`mdrender`, because the
  regression direction (hoisting an import to the top) is the one every linter
  calls compliant. `plugins/claude_code/stream.py` imports `mdrender` at the top
  and `file_fmt.py` defers it: same import, opposite placement, both correct —
  a long-lived tailer pays once, a per-event handler would pay per file op.
- **Registries over if/elif ladders.** Type/event switches are data:
  `dispatch._ROUTES`, `tools.RENDER_KINDS`, `Renderer._USE`/`_RESULT`,
  `audit.COMMANDS`, `audit.ANOMALY_SECTIONS`, `transcript._CONV`/`_FOLD`
  (the two presenters of `parse_line`'s declared `KINDS`), and the
  dashboard's two HTTP planes (`_FIXED_GET`/`_SESSION_GET`,
  `_FIXED_POST`/`_SESSION_POST` —
  docs/dashboard.md *Routing*). A new case is one registration, and ordering
  (when load-bearing) is explicit in the table, with a test pinning the
  sequence. What stays an explicit match is a route whose SHAPE is the
  routing — a variable-arity path whose tail is a name, not a verb — and the
  table's own signature must then be uniform enough to call blind.
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
- **A DB path is absolute, always.** `state._connect` refuses a relative one
  (returning None like any other failure): it CREATES what it opens, so a
  relative path — the shape an unresolved/empty `MIRROR_LOG` produces — puts a
  live session's DB wherever the process's cwd points. That is never where a
  session lives and is usually somebody's working tree (it left a real
  `tests/.state.db` in the checkout, gitignored and unnoticed; the dashboard
  singleton's cwd is the main checkout). Derive paths from `core/paths.py`,
  which only ever returns absolute ones.

## Files and encodings

- **Every `open()` names its encoding — `utf-8` — or opens in binary.** Text
  mode without `encoding=` uses the *locale* default, which makes the bytes on
  disk depend on the environment that happened to run the process. Nothing here
  can afford that: the sources, transcripts, ops and fixtures are full of
  non-ASCII (`⧉ ✉ ▪ ⇢`, the Kazakh in docs/), and `bin/retarget-python.py` is a
  read-modify-**write** over the repo's own sources, where a decode/encode
  mismatch corrupts every `bin/` entry it touches. Both halves of a
  read-modify-write pair must agree, explicitly.
- Binary is the right answer when the bytes *aren't* text: `cmd_pre.py` touches
  the tee target with `"ab"` because that stream is raw command output. Say it
  in binary rather than declaring an encoding the data doesn't have.
- Tests are held to the same rule — they assert on that same non-ASCII content,
  and a fixture is where the next `open()` gets copied from.
  `test_every_open_names_its_encoding` walks every tracked `.py` with `ast` and
  flags text-mode opens with no `encoding=` (ruff's `PLW1514` is preview-gated;
  parsed rather than grepped, because the *mode* argument decides).

## Magic values and deliberate divergence

- **A knob lives with its READER, not in a constants module.** The
  dashboard's `config.py` is a REGISTRY for facts the whole tier shares or
  that the page must be served (`UPLOAD_MAX`/`RENAME_MAX` via
  `/api/limits`) — not a home for every constant in the package. A knob
  exactly one module reads belongs to that module, the way
  `notify/presence.py` owns `VIEW_TTL_S` and `http/post/interrupt.py` owns
  the interrupt/verify timings: the module-qualified read rule then points
  a test at the code the number actually governs. Same for a FUNCTION —
  `clip_screen` is about captured screens, so it sits with the screen-drive
  plumbing (`screendrive.py`), not beside the ports and cadences.
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
- **A file pointer in a comment must resolve.** Comments and docs here name
  files constantly ("see `core/streamfmt.py`") — that is how the next reader
  finds an owner, so a pointer to a path that no longer exists is worse than
  no pointer: it implies a shape the repo no longer has. These rot by RENAME,
  silently and in bulk (the dashboard decomposition left ten behind, including
  this file assigning the no-emoji rule to a module that had become a
  package). `test_file_pointers_in_comments_resolve` walks every tracked text
  file and fails on one that doesn't exist — so **moving a module means
  regrepping its name**, and a pointer used as data (a path fed to a parser,
  the upstream `docs/en/` tree) is listed in that test's skip set.
