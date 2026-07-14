# Testing

The e2e suite (`tests/`, `make test`) drives the real hook scripts as
subprocesses with synthetic payloads and asserts on the three state surfaces
(session state DB, tab DB, audit DB). To run hermetically and fast it uses four
env knobs that exist **only for the test suite** — nothing sets them in a real
session, and unset they leave shipped behavior bit-identical:

| Env var | Default | Effect |
|---|---|---|
| `CLAUDE_MIRROR_TMPDIR` | `/tmp` | Relocates everything `claude_paths.py` derives: `claude-mirror-<key>.log*` state DBs/sidecars/parks **and** the global `claude-kitty-tab.db` — per-test isolation |
| `CLAUDE_TAIL_POLL_S` / `CLAUDE_TAIL_BACKSTOP_S` | `0.4` / 6 h | `claude_tail.py` poll cadence / absolute tailer cap |
| `CLAUDE_STREAM_GRACE_S` | 2 s (fg/bg) · 8 s (monitor) | `claude-stream.py` idle-grace before writer-gone is definitive |
| `CLAUDE_WATCH_POLL_S` | unset | One value replacing every `claude-tab-status.py` watcher/grace sleep (bg-watch 2 s, interrupt-watch 0.5 s, bg-recheck grace 4 s) |
| `CLAUDE_CODEX_GRACE_S` | 8 s | `plugins/codex/stream.py` rollout completion grace (close the block if no new turn follows `task_complete`) |
| `CLAUDE_OTEL_PORT` / `CLAUDE_OTEL_GRACE_S` | 4319 / 900 s | The OTLP receiver's bind port / idle-exit timeout (`plugins/otel/receiver.py`). `test_l5_otel.py` picks a free port per test and a short grace so a spawned receiver never lingers; the receiver only spawns when `CLAUDE_CODE_ENABLE_TELEMETRY=1`, which the suite never sets, so it stays inert unless a test opts in |

Any session started with the timing knobs set is self-evident in the audit:
`session_start` captures `CLAUDE_TAIL_*`/`CLAUDE_STREAM_*`/`CLAUDE_WATCH_*`/
`CLAUDE_CODEX_*`/`CLAUDE_OTEL_*` (and all `CLAUDE_MIRROR*`) into the `sessions.env` column. The fake terminal
side is injected via the pre-existing `KITTY_KITTEN_BIN` override (a recorder
script standing in for `kitten`), so no product code special-cases tests.
