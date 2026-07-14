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
  `bin/` scripts may import anything.
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
| Session-alive probe | `core/state.parked()` — a bare exists check, never a connect |
| Audit-import degradation | `core/noaudit.load_audit()` — the ONLY way to get `A`; direct `from core import audit` is reserved for `bin/claude-audit.py` |
| Audit table set | `core/audit._SCHEMA` — derive lists (`prunable_tables()`, `WRITE_COMMANDS`), never hand-copy |
| CSI/OSC escape grammar | the named fragments in `core/render.py` composing `_ANSI`/`_CTRL` |
| Tailer env contract `CLAUDE_STREAM_*` | `hookkit.stream_env()` — launchers pass the raw command, never the render decision |
| Usage dedup + Σ-row arithmetic | `accounting.usage_fold` + `ops.split_tokens` |
| settings.json env-block layering | `model.settings_env` (`nearest_only=` preserves split.py's walk) |
| File-op payload shapes, `FILE_LABEL`/`FILE_RGB` | `plugins/claude_code/tools.py` |
| Click-to-view stash-and-link | `file_fmt.stash_view` (over the shared `view_ops`) |

Adding a new shared fact? Give it one owner in the most-core module whose
charter fits, document the owner here, and (if cheap) add a grep test.

## Module shape

- **No import-time side effects.** The dispatcher imports every handler module
  on every hook event, and tests import modules in isolation — so at import
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
  `core.tail.stream_lifecycle` + `hookkit.spawn_streamer` (stream rows and
  crash audit come free). New handlers go through `hookkit.run()`.
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

## Docs

- `docs/` is the design record: update the mechanism's doc **in the same
  commit** as a behavior change, including the "why not X" when an
  alternative was considered and rejected.
- CLAUDE.md's module lists, this file's ownership table, and the audit-debug
  skill (schema table AND bug-shape playbook — both) are part of the change,
  not follow-ups.
