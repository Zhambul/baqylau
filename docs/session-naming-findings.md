# Claude Code session naming — findings & implementation brief

**Goal:** control how a Claude Code session is labeled — both (a) the name shown in the
`claude --resume` picker and (b) the live kitty tab/window title — ideally set
programmatically by a script/hook (not only by a human typing `/rename`).

**Status:** research + on-disk verification complete — and since 2026-07-18 **partially
implemented in THIS repo** as the web dashboard's rename button (docs/dashboard.md *Web
rename*): the JSONL-append + `set-tab-title` composition recommended in §5, with the
`agent-name` writer living next to its parser (`plugins/claude_code/transcript.set_session_title`,
single-owner + grep-test-enforced), the endpoint at `dashboard/server.py post_rename`, and
`web-rename` audit rows. The SessionStart-hook auto-naming half remains unimplemented (its
intended target was a *separate* hooks/scripts project).

> Environment where this was verified: macOS, kitty, Claude Code with
> `CLAUDE_CODE_SESSION_ID` present, session file format as of 2026-07. **Field names and
> behaviors below are version-specific** — Anthropic documents the on-disk format as internal
> and unstable. Re-verify before relying on it.

---

## TL;DR

- **Supported, safe path:** a **`SessionStart` hook** returning
  `hookSpecificOutput.sessionTitle` sets the session name at startup/resume. This is the only
  officially documented programmatic naming mechanism.
- **`SessionEnd` cannot rename** — it fires after teardown; its output is ignored for naming
  (confirmed in docs and by closed issue #25450).
- **Claude shelling out to `claude -p --resume <id> "/rename"` to rename *itself* is NOT
  viable** — it either targets a throwaway new session, or races/corrupts the live transcript,
  and the live session id isn't reliably exposed to Bash in a plain terminal.
- **The name lives in the session JSONL** and a script *can* append a rename record at any
  time. On this installed version the record is `agent-name`/`agentName` (NOT the
  `custom-title` some blogs claim). This reaches the **picker on next resume**, but does **not**
  relabel the **live tab**.
- **The kitty tab title is a separate channel** — Claude Code emits an OSC title escape; kitty
  shows it as the window/tab title. This repo never sets it (only `set-tab-color`). To move the
  live tab immediately, use `kitten @ set-tab-title`, which also makes the tab stop following
  Claude Code's OSC updates (sticky override).
- **A single script can set both at once** (tab via kitten socket + picker via JSONL append).
  The two have unavoidably different timing (tab = instant, picker = next resume) and different
  risk profiles (tab = safe, JSONL append on a live session = unsupported/concurrent-write).

---

## 1. What controls the `--resume` picker name

### Officially supported ways to name a session
| When | How |
|---|---|
| At startup | `claude -n <name>` (or `--name`) |
| Mid-session (interactive) | `/rename <name>` (also on the prompt bar) |
| From the picker | highlight session, press **Ctrl+R** |
| Non-interactive | `/rename` via `claude -p` (v2.1.205+) — *caller*-initiated |
| Auto from a plan | accept a plan in plan mode → named from plan (only if not already named) |
| **Hook (programmatic)** | **`SessionStart` hook → `hookSpecificOutput.sessionTitle`** |

If never named, the picker shows the conversation summary / first prompt plus metadata
(last-activity time, message count, git branch).

### The supported programmatic mechanism: SessionStart hook
```json
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "sessionTitle": "auth-refactor"
  }
}
```
- Applies only when `source` is `"startup"` or `"resume"`; **ignored on `"clear"` and
  `"compact"`**.
- The hook input carries `session_title` — check it's empty first so you don't clobber a
  user-set name.
- Docs: SessionStart output fields, https://code.claude.com/docs/en/hooks.md

### What does NOT work
- `claude session rename <id> <name>` CLI — **does not exist**.
- **`SessionEnd` hook renaming** — SessionEnd "cannot block or control behavior — the session
  is already terminating." Its only output fields are `additionalContext` and
  `terminalSequence`; **no `sessionTitle`**. Issue #25450 (PreSessionEnd / rename-on-exit) was
  closed *not planned*.
- **Claude renaming *itself* via `claude -p`:**
  - `claude -p "/rename x"` with no resume flag → names a **new throwaway** session.
  - `claude -p --resume <id> "/rename x"` → resuming an *active* session makes "messages from
    both interleave into one transcript" (docs, "Branch a session") — corruption risk, no
    protective lock.
  - The live session id isn't reliably in Bash: `CLAUDE_CODE_BRIDGE_SESSION_ID` only exists
    under a Remote Control connection. (**But see §3 — `CLAUDE_CODE_SESSION_ID` *is* present in
    this environment.**)

---

## 2. On-disk session format (verified on this machine)

**Path:**
```
~/.claude/projects/<project-hash>/<session-id>.jsonl
```
- `<project-hash>` = the absolute cwd with **both `/` and `.` replaced by `-`**
  (e.g. `/Users/z.yermagambet/code/personal/baqylau` → `-Users-z-yermagambet-code-personal-baqylau`).
  The `.`→`-` rule matters for usernames/paths containing dots.
- `<session-id>` = the session UUID (also the filename stem).

**Naming record types actually present** (surveyed across real session files here):
```json
{"type":"ai-title","aiTitle":"Run process as background command","sessionId":"031c9c44-..."}
{"type":"agent-name","agentName":"kitty-mirror-e2e-tests","sessionId":"3b0a92d0-..."}
```
- `ai-title` / `aiTitle` — the **auto-generated** summary Claude Code writes itself. This is
  what the picker falls back to and what the kitty tab currently mirrors.
- `agent-name` / `agentName` — the **custom name**; this is the channel `/rename` writes.

> ⚠️ **The field names differ from community blogs.** A widely-cited post claims
> `{"type":"custom-title","customTitle":...}`. That was **not** found on this installed
> version — here it is `agent-name`/`agentName`. Treat the vocabulary as version-fragile:
> a script should grep the live type vocabulary before writing, not hardcode.

**To set a custom name from a script**, append one line:
```json
{"type":"agent-name","agentName":"<name>","sessionId":"<session-id>"}
```
- `sessionId` must match the filename.
- Last matching entry wins (append overrides).
- Reported overwrite rule: an `ai-title` can replace an `ai-title`, but should not clobber a
  user/custom name — using the `agent-name` channel is the "user set it" signal, so it sticks.

**Effect timing:** reaches the **`--resume` picker on next launch** (picker reads files
fresh). Does **not** relabel a **live** session's tab (see §4).

**Community tools that use exactly this** (confirmed working):
- `claude-rename` — https://github.com/sathwick-p/claude-rename — **Stop hook** spawns a
  background worker that calls Haiku on the first exchange, generates a slug like
  `fix-stripe-webhook-retry`, writes it to the session file. `suppressOutput:true`, idempotent
  (names once). This is the safe "name from conversation content" pattern — it does *not* shell
  out to `claude -p`.
- `rename-session` skill — https://github.com/enkira-ai/claude-plugins — renames a session **by
  id without resuming**, by appending a rename record to its JSONL.

**Open feature requests** (community wants a first-class API): #33165 (let Claude rename its own
session), #35316, #44786, #58588 (settings-based session name+color), #24872. Known bug #26134
(`/rename` sometimes doesn't persist for `--resume <name>` lookup).

---

## 3. Can a script (or Claude) locate its own session file? — Yes

Verified available inside a Bash tool call in this environment:
- **`CLAUDE_CODE_SESSION_ID`** = the session UUID (matched the live `.jsonl` filename exactly).
- **cwd** → project hash via `pwd | sed 's#[/.]#-#g'`.
- Assembled path existed and contained this session's `ai-title` record.

```sh
sid="$CLAUDE_CODE_SESSION_ID"
hash="$(pwd | sed 's#[/.]#-#g')"
file="$HOME/.claude/projects/$hash/$sid.jsonl"
```

**Guard:** `CLAUDE_CODE_CHILD_SESSION` was also set (`=1`). In nested/child contexts
`CLAUDE_CODE_SESSION_ID` may point at a sub-session rather than the top-level session — detect
and bail (or resolve upward) before writing.

---

## 4. The kitty tab title is a *different* source

Observed: the kitty tab title equals the session `aiTitle`
(e.g. "Research session naming configuration for resume").

**Where it comes from — NOT this repo:**
- This repo only ever calls `kitten @ set-tab-color` (20 call sites); it never sets a tab/window
  title.
- `kitty.conf` uses the default `tab_title_template`, which renders `{title}` = the active
  **window** title.
- That window title is set by **Claude Code emitting an OSC title escape** (`ESC]2;…`/`ESC]0;…`)
  carrying its in-memory session title. kitty shows it as the window title; the tab template
  echoes it. It matches `aiTitle` because Claude Code derives the OSC title from the same
  in-memory session title.

**Chain:**
```
session title (in-memory)  ──►  OSC escape emitted by Claude Code  ──►  kitty window title  ──►  tab title
        ▲
        └── seeded from the JSONL at startup; /rename updates it live
```

**Does editing the JSONL rename the live tab?** No. The tab reflects the last OSC string Claude
Code emitted, which tracks the *in-memory* title. Appending to the JSONL of a running session
does not cause a re-emit. It updates on next relaunch/`--resume`, or when an in-session
`/rename` re-emits the OSC.

| Action | Live tab title | `--resume` picker |
|---|---|---|
| `/rename` in-session | ✅ updates now (re-emits OSC) | ✅ |
| Edit JSONL externally | ❌ not until relaunch | ✅ next launch |
| `kitten @ set-tab-title` | ✅ immediate | ❌ (tab only) |

**To move the live tab immediately:** `kitten @ set-tab-title`. Important kitty behavior: once
you set an explicit tab title, the tab **stops following the active window title** — so it will
NOT be clobbered by Claude Code's subsequent OSC emits (sticky). Trade-off: that tab also stops
reflecting future `ai-title` changes — you've taken manual ownership of the title for the rest
of the session.

---

## 5. Setting BOTH at once — feasibility & design

Feasible with a single script. Both inputs verified present here:
`KITTY_LISTEN_ON` (e.g. `unix:/tmp/kitty-79463`), `KITTY_WINDOW_ID` (e.g. `82`),
`CLAUDE_CODE_SESSION_ID`, and `kitten` on PATH.

**Script outline (design, not final code):**
1. **Resolve identity**
   ```sh
   sid="$CLAUDE_CODE_SESSION_ID"                 # guard CLAUDE_CODE_CHILD_SESSION
   hash="$(pwd | sed 's#[/.]#-#g')"
   file="$HOME/.claude/projects/$hash/$sid.jsonl"
   ```
2. **Tab (immediate, safe)** — target *this* session's tab specifically:
   ```sh
   kitten @ --to "$KITTY_LISTEN_ON" set-tab-title \
     --match "window_id:$KITTY_WINDOW_ID" "$NAME"
   ```
   (Same socket + match discipline this repo already uses for `set-tab-color`.)
3. **Picker (next resume)** — append the rename record:
   ```sh
   printf '%s\n' "{\"type\":\"agent-name\",\"agentName\":\"$NAME\",\"sessionId\":\"$sid\"}" >> "$file"
   ```
   Prefer greping the live type vocabulary first instead of hardcoding `agent-name`.

**Caveats (must be stated to any implementer):**
1. **Asymmetric timing is inherent** — tab instant, picker next resume. Same name, two moments.
2. **Concurrent-write risk on the JSONL** — appending to a *live* session file races Claude
   Code's own writes. A single atomic append line is low-risk, but the safe window is when the
   session is not mid-turn (e.g. from a Stop hook's settle path). The tab write carries no such
   risk.
3. **Format fragility** — `agent-name`/`agentName` is version-specific; verify before trusting.
4. **Override semantics** — after `set-tab-title`, that tab ignores Claude Code's titles for the
   rest of the session (usually desired for a deliberately-named session).
5. **Cross-project note** — this repo historically never wrote the session JSONL (only its own
   `/tmp` state DBs). The web rename (2026-07-18) is now the ONE sanctioned exception: a single
   atomic append through the record shape's owner (`transcript.set_session_title`), with
   `web-rename` audit rows per the repo's audit-coverage rule (docs/dashboard.md *Web rename*).
   Any OTHER JSONL write still needs the same scrutiny — the safety caveats above stand.

**Recommended lowest-risk composition:** always set the **tab** (safe, instant); append the
**`agent-name`** line only when **not mid-turn** (e.g. Stop hook settle), so the picker catches
up without racing the live writer.

---

## 6. Sources

Official docs:
- Hooks (SessionStart/SessionEnd output fields): https://code.claude.com/docs/en/hooks.md
- Manage sessions (name, picker, transcript storage, branch): https://code.claude.com/docs/en/sessions.md
- Headless / `-p` + `--resume`: https://code.claude.com/docs/en/headless.md
- Env vars: https://code.claude.com/docs/en/env-vars.md

Community:
- claude-rename (Stop-hook auto-titler): https://github.com/sathwick-p/claude-rename
- rename-session skill / plugins: https://github.com/enkira-ai/claude-plugins
- JSONL format writeup (note: uses the older `custom-title` field): https://blog.vincentqiao.com/en/posts/claude-code-rename
- Issues: #25450 (rename-on-exit, closed not-planned), #33165, #35316, #44786, #58588, #24872, #26134

Empirical (verified on this machine, 2026-07-10): `CLAUDE_CODE_SESSION_ID` present; project-hash
= cwd with `/` and `.` → `-`; session file contained `ai-title`/`agent-name` records (NOT
`custom-title`); this repo calls only `set-tab-color`; kitty tab title mirrors Claude Code's OSC
title escape.
