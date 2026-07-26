# Testing

The e2e suite (`tests/`, `make test`) drives the real hook scripts as
subprocesses with synthetic payloads and asserts on the three state surfaces
(session state DB, tab DB, audit DB). `make test` runs it in parallel
(pytest-xdist `-n auto`) — safe because every test is tmpdir-isolated;
`make test-seq` is the sequential fallback. To run hermetically and fast it
uses env knobs that exist **only for the test suite** — nothing sets them in a
real session, and unset they leave shipped behavior bit-identical:

| Env var | Default | Effect |
|---|---|---|
| `CLAUDE_MIRROR_TMPDIR` | `/tmp` | Relocates everything `core/paths.py` derives: `claude-mirror-<key>.log*` state DBs/sidecars/parks **and** the global `claude-kitty-tab.db` — per-test isolation |
| `CLAUDE_TASKS_GLOB_ROOT` | `/private/tmp/claude-*` | Root of the glob `claude-stream.py`/`claude-cmd-fmt.py` use to find Claude Code's `tasks/<id>.output` files (`plugins/claude_code/stream.py glob_task_output`). The default mirrors Claude Code's OWN on-disk layout (external, empirically macOS) — not a path this repo mints, so it lives in `stream.py`, not `core/paths.py`. The suite points it into the per-test sandbox so the `task_dir` fixture never creates dirs on shared host `/tmp` |
| `CLAUDE_TAIL_POLL_S` / `CLAUDE_TAIL_BACKSTOP_S` | `0.4` / 6 h | `tail.py` poll cadence / absolute tailer cap |
| `CLAUDE_TAIL_WAIT_POLL_S` | `0.2` | `tail.py wait_for` source-appearance poll (deliberately faster than `POLL_S` — runs only until the file lands) |
| `CLAUDE_TAIL_PUMP_MAX_B` | 256 KB | `tail.py FileTailer` per-pump read ceiling — one pump ingests at most this much; `tail.capped` tells the caller to keep pumping before trusting completion signals ([streaming.md](streaming.md), *Worst-case bounds*). Unit tests shrink it by monkeypatching the module constant rather than env |
| `CLAUDE_TAIL_LINE_MAX_B` | 64 KB | `tail.py FileTailer` max surfaced line, opt-in per tailer (`line_max=`; only `claude-stream.py` sets it — JSONL tailers must not). Over-cap lines get an `… (N bytes elided)` marker |
| `CLAUDE_STREAM_OP_MAX_B` | 128 KB | `claude-stream.py verbatim_batches` — max raw bytes per verbatim `gut` op; a bigger pump batch splits into multiple ops |
| `CLAUDE_STREAM_GRACE_S` | 2 s (fg/bg) · 8 s (monitor) | `claude-stream.py` idle-grace before writer-gone is definitive |
| `CLAUDE_STREAM_LSOF_S` | 1 s | `claude-stream.py has_writer` lsof re-check throttle — `lsof` scans the whole fd table, and unthrottled per-tick calls from several concurrent tailers were the CI lsof storm (once one lsof exceeds its timeout, "assume still writing" starves writer-gone indefinitely — the flake class no wait-ceiling fixes) |
| `CLAUDE_WATCH_POLL_S` | unset | One value replacing every `claude-tab-status.py` watcher/grace sleep (bg-watch 2 s, interrupt-watch 0.5 s, bg-recheck grace 4 s) |
| `CLAUDE_CODEX_GRACE_S` | 8 s | `plugins/codex/stream.py` rollout completion grace (close the block if no new turn follows `task_complete`) |
| `CLAUDE_CODEX_WATCH_POLL_S` / `CLAUDE_CODEX_RO_GRACE_S` | `0.4` / 8 s | `plugins/codex/watch.py` discovery poll cadence / companion grace (how long a rollout with no companion job waits before being adopted as TUI-origin) |
| `CLAUDE_STREAM_PARENT_SCAN_S` | 2 s | `plugins/claude_code/substream.py` throttle on the parent-transcript `tool_result` scan (the rejected/abandoned-Task fallback end signal) |
| `CLAUDE_OTEL_PORT` / `CLAUDE_OTEL_GRACE_S` | 4319 / 900 s | The OTLP receiver's bind port / idle-exit timeout (`plugins/otel/receiver.py`). `test_l5_otel.py` picks a free port per test and a short grace so a spawned receiver never lingers; the receiver only spawns when `CLAUDE_CODE_ENABLE_TELEMETRY=1`, which the suite never sets, so it stays inert unless a test opts in |
| `CLAUDE_TEST_WAIT_SCALE` / `PYTEST_TIMEOUT` | 1x (6x when `CI=true`) / 30 s | `conftest.wait_until`'s timeout multiplier for slow shared runners, and pytest-timeout's per-test budget. They must move in LOCKSTEP: an unscaled 30s budget kills a slow-but-passing scaled wait as an opaque pytest-timeout thread dump before its 60s ceiling is reachable (the macOS-runner flake class). The CI workflow sets `PYTEST_TIMEOUT=180`; pinned by `test_pytest_timeout_budget_outlives_scaled_waits` |

Any session started with the timing knobs set is self-evident in the audit:
`session_start` captures `CLAUDE_TAIL_*`/`CLAUDE_STREAM_*`/`CLAUDE_WATCH_*`/
`CLAUDE_CODEX_*`/`CLAUDE_OTEL_*` (and all `CLAUDE_MIRROR*`) into the `sessions.env` column.

**In-process audit writes are sandboxed too** (2026-07-16): subprocesses get
their hermetic `CLAUDE_AUDIT_DIR` from `test_env`, but a unit test calling
audit-writing product code *directly* (e.g. `spawn_detached`'s script-missing
degrade row) used to hit the REAL `~/.claude/baqylau-audit` DB — and such rows
are global (no sid), so every live session's ⚠ warning light surfaced the
suite's own deliberate error rows. The autouse `_fresh_audit_conn` fixture now
points `CLAUDE_AUDIT_DIR` at a per-test sandbox for the in-process side as
well (tests needing a specific dir monkeypatch over it), and
`test_spawn_detached_missing_script_returns_none` pins the guarantee.
The same fixture sandboxes **`CLAUDE_CONFIG_DIR`** in-process (2026-07-18):
the pytest process inherits the launching shell's value, which under the
claude-subscription switcher is `configs/<slug>` — whose `settings.json` is a
SYMLINK to the real `~/.claude/settings.json`. A dashboard test that seeded
"the hermetic config dir's settings.json" through the ambient
`os.environ["CLAUDE_CONFIG_DIR"]` truncated the user's real settings file
(hooks, env, statusLine — everything) to its one seeded key. In-process
settings reads and writes now default to a per-test `config-inproc/` dir. The fake terminal
side is injected via the pre-existing `KITTY_KITTEN_BIN` override (a recorder
script standing in for `kitten`), so no product code special-cases tests.
Calls that take the RAW `@kitty-cmd` socket path (`frontends/kitty.py
_rc_raw` — get-text, the freeze-bracket scrolls, the tab paint) never spawn
`kitten`, so the recorder can't see them; the `fake_rc_socket` fixture
(`conftest.FakeRCServer`) stands up a live AF_UNIX socket speaking the DCS
framing, records every decoded command envelope, and replies with a
programmable `{"ok": …}` — wire it into `KITTY_LISTEN_ON` alongside the
recorder to assert exact raw frames (`test_l0_frontends_contract.py`,
`test_l3_tab.py`). Without it, the default dead socket path makes the raw
attempt miss and every call falls back to the recorder — which is why the
pre-raw-path tests keep passing unchanged.

**No DB may land in the repo working tree** (2026-07-25). The suite is hermetic
by construction — every DB path comes from a per-test tmpdir or the fake
`CLAUDE_MIRROR_TMPDIR` — but a test that hands product code an **unresolved log**
gets its DB wherever the process's cwd points, which is the checkout. That
happened twice and went unnoticed for weeks, because `*.db` is gitignored: an
80KB `tests/.state.db` sat there accumulating rows *shared by every run and every
xdist worker*, which is precisely the cross-run coupling a hermetic suite exists
to rule out. The cause is worth remembering: `_load_scorebar` execs
`bin/claude-scorebar.py` in-process, and a pane-renderer entry parses `sys.argv`
**at import** (its sanctioned assembly-layer shape) — so it read *pytest's* argv
and `pytest tests/` became `MIRROR_LOG="tests/"` (`pytest tests/<file>.py` left
the matching `tests/<file>.py.state.db`). Two guards now: `core/state._connect`
refuses a RELATIVE path outright (it creates what it opens, and no session ever
lives in a relative path — the dashboard singleton's cwd is the main checkout),
and `conftest.pytest_sessionfinish` fails the run listing any DB found under the
repo. A test that loads a pane entry pins argv and passes a `tmp_path` log.

**Import discipline is pinned by OUTCOME, twice** (2026-07-25).
`test_import_safety.py` had one half: importing a hook/streamer module in a fresh
interpreter with a sabotaged `frontends.get`/`open`/`sqlite3.connect` must still
succeed — that checks the *side-effect* rule directly, rather than policing where
`import` lines sit. The cost half was missing, and it is the direction that
actually regresses: hoisting `from core import mdrender` to the top of a per-event
handler satisfies **every** linter (`PLC0415` is ignored repo-wide, and the hoist
is the compliant direction anyway) while adding ~40ms of `wenmode` to every file
op, several times per turn. `test_per_event_imports_stay_off_the_heavy_renderers`
asserts the per-event modules' import graph contains no
`wenmode`/`pygments`/`core.mdrender`. A **module-set** check, not a timing
threshold — deterministic, no flake, and it names the one hot spot instead of
budgeting all 90 function-level imports. Measured baselines, for calibration: a
bare interpreter is ~24ms, the whole `dispatch` graph ~11ms on top of that,
`frontends`/`core.render` ~0, and `wenmode` alone ~40ms.

**Fixtures obey the product's file rules too** (2026-07-25). The 87 test-side
`open()` calls that used the locale default now name `utf-8`, for the reason in
docs/styleguide.md *Files and encodings* plus one specific to tests: they assert
on the very non-ASCII content a locale mismatch would mangle, and a fixture is
where the next `open()` gets copied from. `test_every_open_names_its_encoding`
walks every tracked `.py` with `ast`, and `test_open_encoding_walker_exemptions`
pins what it must *not* flag (binary mode, an explicit non-utf-8 encoding, a mode
hidden behind `**kwargs`) — the exemptions being the whole content of the rule.
Note for anyone doing a bulk rewrite of call sites: match the AST node by its
`func`, not by the position a linter reports. For `open(p).read()` the reported
column belongs to the *outer* chained call, so a position-keyed rewrite lands
`encoding=` inside `.read()` — 15 syntax errors, all pointing at the wrong line.

**One test executes JavaScript, on purpose, and skips without it** (2026-07-25).
The web mirror's view modes (docs/dashboard.md *View modes*) compute their
collapse in the page — which adjacent items become one run, what the summary line
says, which colour the dot is — because only the client holds the assembled feed
(ops arrive as SSE increments, history as separate pages, and a run routinely
straddles two responses). That put the feature's core logic somewhere the suite
could previously only *grep*, and a grep cannot distinguish a correct run cut
from an off-by-one. So `tests/jsdom/viewmode.js` shims ~60 lines of DOM (classes,
`dataset`, class-selector queries) plus the handful of app globals the engine
calls, `vm`-runs the REAL `app.05-session.js`, and prints a JSON verdict that
`test_view_mode_engine_collapses_runs_and_words_them` asserts on. It
`pytest.skip`s when `node` is not on PATH, so node is NOT a suite dependency and
`requirements-dev.txt` is unchanged — the Python-side tests (classifier,
endpoint, vocabulary parity) still cover everything reachable from Python. Adding
a second such test is fine; adding a JS test FRAMEWORK is not the same decision
and should be taken separately.

A third drives the new-session FORM: `tests/jsdom/newsession.js` builds the real
modal out of `app.09-newsession.js` and then presses its two cross-phase gestures
(the fresh/resume toggle, the launch). `openNewSession` used to be one 344-line
function; splitting it into named phases means each phase hands the next a
context object, and a missed hand-off is a ReferenceError no other check can
see — `node --check` reads syntax, not scope, and a grep cannot tell that a
phase reads a name nobody passed it. It caught exactly that twice while the
split was being made.

There is now a second one, on the same terms: `tests/jsdom/sections.js` drives
the SECTIONS engine (the monitors/jobs/memory secondary tabs in
`app.11-chrome.js`) behind
`test_secondary_tab_sections_are_one_engine`. It exists for the same reason —
those two tabs were fourteen near-identical function pairs that got folded onto
one descriptor, and a grep cannot tell that the jobs grid still says "no
background jobs" rather than the monitors wording, or that the poll still stops
when nothing is live.

And a fourth: `tests/jsdom/asksubmit.js` runs `submitAsk` out of
`app.07-dialogs.js` and reports the POST body it builds, behind
`test_ask_submit_never_discards_picked_answers`. It exists because that
function can silently send LESS than you answered: an ask-wide preview test
plus a chat escalation that omits `answers` threw away every picked option the
moment one word was typed (docs/dashboard.md, *Web ask*). That is a bug about
which branch a compound condition takes and what the branch leaves out of the
body — invisible to a grep, and invisible to the Python suite too, since no
server round-trip is involved. Executing the function and asserting on the body
is the only thing that catches it; the harness is checked against the OLD
source as well, to confirm it fails there.

The DOM shim these harnesses share lives in
`tests/jsdom/domshim.js` (`El` + `domGlobals()`): a copy per harness would be
exactly the duplication these harnesses were written to catch. Note that a
`const` at a source's top level is a LEXICAL binding and never becomes a
property of the `vm` global the way a `function` declaration does — a harness
that needs one appends its own `globalThis.X = X` to the evaluated text so both
share a scope.
