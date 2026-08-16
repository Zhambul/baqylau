# Handover — hooks are working again; continue the refactor from here

## What happened

Session `31d72ad3` was mid-refactor (step 2 of the plan: `plugins/ → harness/impl/`).
The `git mv` renamed `plugins/claude_code/canonical_hook.py` out from under the
running Claude Code session. Every subsequent hook invocation
(`PostToolUse`/`PostToolBatch`/`UserPromptSubmit`/…) failed with
`python3: can't open file '…/plugins/claude_code/canonical_hook.py': No such file or directory`.
Claude Code treats a failing PostToolUse hook as a **blocking error**, so the session
froze at 23:13:33 (last recorded hook raw event, id 14676). Evidence for the failure:
`raw_events` shows hook delivery stops at that instant while `transcript`/`otel` keep
flowing to 23:19:06 (daemon was alive; only the hook *client* path was dead).

## What I fixed (hooks now deliver again — verified end-to-end)

1. **`~/.claude/settings.json`** — all 28 hook entries and the `statusLine` command
   now point at `harness/impl/claude_code/hooks/entry.py` and
   `harness/impl/claude_code/hooks/statusline.py` (was `plugins/claude_code/…`).
2. **`~/.codex/hooks.json`** — all 10 hook entries now point at
   `harness/impl/codex/hooks/entry.py` (was `plugins/codex/canonical_hook.py`).
3. **Forwarding shims at the old paths** for sessions that launched BEFORE the
   config update — Claude Code/Codex capture the hook command at session start and
   cache it for the process lifetime, so editing `settings.json` mid-session has no
   effect on the already-running session. The shims forward to the moved entries:
   - `plugins/claude_code/canonical_hook.py` → `harness/impl/claude_code/hooks/entry.py`
   - `plugins/codex/canonical_hook.py` → `harness/impl/codex/hooks/entry.py`
   - `plugins/claude_code/statusline.py` → `harness/impl/claude_code/hooks/statusline.py`
   Delete the shims once the old sessions are gone (they're the plan's option b).
4. Import rewrites in the **hook entry closure** (these modules run in a fresh
   process on every hook delivery, so they had to import from the new layout):
   - `harness/impl/claude_code/hooks/entry.py` — `plugins.* → harness.impl.*`,
     sys.path anchor fixed (file moved 4 levels deep; now `Path(__file__).parents[4]`).
   - `harness/impl/codex/hooks/entry.py` — same; dropped unused `os`.
   - `harness/impl/claude_code/hooks/gateway.py` — `foreground`/`model` imports +
     `contracts.harness → harness.contract/models` (gateway's own stale imports).
   - `harness/impl/claude_code/hooks/foreground.py` — `plugins.claude_code.shell`.
   - `harness/impl/claude_code/hooks/statusline.py` — `account`, `usage_state →
     harness.impl.claude_code.usage.state`, sys.path anchor.
   - `harness/impl/claude_code/launcher.py` — `account` + `contracts.harness`.
   - `harness/impl/claude_code/usage/state.py` — `application_data →
     harness.impl.claude_code.data`.
   - `harness/impl/claude_code/account.py` — lazy `usage_state` import.

**Verified**: ran the claude + codex entries as a subprocess with a real hook payload;
the daemon recorded the raw events (then I deleted the test rows). `ruff check` clean
on all touched files. Both external configs parse as JSON.

## What you still have to do (the rest of the refactor)

The daemon is **still running the pre-rename code in memory** — it imports
`plugins.*` and will keep serving hooks until it restarts. **Do not restart it yet**
unless step 2 is done, or hook delivery dies again.

1. **Finish step 2's discovery move**: `app/plugins.py` still globs
   `plugins/*/plugin.py` and imports `plugins.<name>.plugin`. It must discover
   `harness/impl/*/plugin.py` and import `harness.impl.<name>.plugin`. (`plugin.py`
   descriptors are the `plugin =` objects already in the moved tree.)
2. **Repo-wide import rewrite** of the still-stale `plugins.*` references inside
   `harness/impl/**` — grep `from plugins\.` / `import plugins\.` under `harness/`
   (the full list is long: `plugin.py`, `catalog.py`, `controller.py`,
   `canonical/translator.py`, `probe.py`, `reactors.py`, `usage/rows.py`,
   `controls/*`, `otel/*`, codex twins). They're not needed for hook delivery
   (that closure is fixed), but they block the daemon and the rest of the app.
   Watch the module renames: `hooks.py → hooks/gateway.py`,
   `canonical.py → canonical/translator.py`, `usage_state.py → usage/state.py`,
   `application_data.py → data.py`, `terminal_probe.py → probe.py`,
   `foreground.py → hooks/foreground.py`.
3. **`runtime/harnesses.py` → `harness/registry.py`**, `app/services.py →
   harness/services/*`, and the terminal panes moves are the plan's steps 2–5.
4. **Step 6 sweep**: `tests/test_canonical_architecture.py:184-185` still asserts the
   OLD path files exist (`plugins/claude_code/canonical_hook.py`,
   `plugins/codex/canonical_hook.py`) and will fail; `tests/test_import_safety.py`
   allowlists the old package names; README/CLAUDE.md/skills still name old paths.
5. When step 2 lands, restart the daemon once so it serves the new layout.

## Judgment calls pending (§5/§6 of the plan)

- Whether to keep pointing configs at internal paths (option a), add forwarding
  shims at the old paths (b), or move to stable `bin/` entry points (c — my
  recommendation for hooks/statusline so config never names an internal path).
- Whether to split `core/wire.py` into `harness/hooks/wire.py`.
- `plugins/claude_code/pane_settings.py` is dead (0 importers) — delete, don't move.
