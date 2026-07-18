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
degrade row) used to hit the REAL `~/.claude/kitty-audit` DB — and such rows
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
