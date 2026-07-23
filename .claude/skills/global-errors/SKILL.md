---
name: global-errors
description: Investigate the ⚠ "global errors" warning light (audit `errors` rows surfaced in every session's scorebar/mirror), then either FIX the root cause or IGNORE a benign, expected-outcome degrade-audit so it stops lighting up. Use when the user reports a persistent ⚠ audit warning, a global error count that won't clear, or asks to "investigate global errors".
---

# global-errors — triage the ⚠ warning light, then fix or ignore

The always-on audit trail records every swallowed exception into the `errors`
table. `core/errwatch.py` is the WARNING LIGHT over that table: it surfaces
errors live as the scorebar's `⚠ N` chip and a `⚠ audit: <script>: <what>`
one-liner in the mirror. Rows with **`session_id=''`** are GLOBAL — an audit
outage or a pre-/cross-session failure that degrades every session — so they are
counted and painted (tagged `global:`) in EVERY session, live and parked. Those
are the "global errors" this skill is about.

Goal: name each global error **from evidence**, decide whether it is a real
failure (→ **fix**) or a normal, expected outcome someone chose to audit (→
**ignore**), and leave the warning light meaningful.

## 1. Investigate

Run from the repo root (`/Users/z.yermagambet/code/personal/baqylau`).

```sh
# What global errors exist, grouped by signature (newest first)?
python3 bin/claude-audit.py sql "SELECT script, func, COUNT(*) n, \
  MAX(datetime(ts,'unixepoch','localtime')) last FROM errors \
  WHERE session_id='' GROUP BY script, func ORDER BY last DESC"

# The full picture for ALL errors (incl. per-session), same grouping:
python3 bin/claude-audit.py sql "SELECT script, func, COUNT(*) n FROM errors \
  GROUP BY script, func ORDER BY n DESC"

# Drill into one signature — traceback + the context dict (args in hand):
python3 bin/claude-audit.py sql "SELECT id, \
  datetime(ts,'unixepoch','localtime') t, session_id, traceback, context \
  FROM errors WHERE func='<func>' ORDER BY ts DESC LIMIT 10"
```

Read each signature and classify it:

- **`traceback` is a real stack** (`Traceback (most recent call last): … / SomeError: …`)
  → a genuinely swallowed exception. This is a **bug to FIX** — find the
  `except` block whose `A.error(..., func=...)` matches and fix the cause. The
  `context` dict carries the args that were in hand. (Fixing stops *new* rows; it
  does NOT empty the already-recorded ones — clear those per §4.)
- **`traceback` is `NoneType: None`** → the row was written by a bare
  `A.error(...)` OUTSIDE any `except` block: a *deliberate degrade-audit* of a
  code path someone wanted to keep debuggable. Now read the code at that `func`
  and decide:
  - it marks a **failure** (a spawn that should have worked, a dead endpoint,
    a write that was refused) → **FIX** the underlying condition, or
  - it marks a **normal, expected outcome** (a "no match, so we just don't show
    the optional thing" return path) → **IGNORE** it (§3). The tell is the
    docstring/comment at the call site: if returning here is documented as fine,
    it does not belong on the warning light.

`grep -rn "func=\"<func>\"\|_audit_once(\"<func>\"" core plugins dashboard` finds
the call site. Note that `func` is the discriminating field — `script` is just
whichever long-lived process wrote the row (`claude-scorebar.py`,
`claude-dashboard.py`).

## 2. Where the warning light is surfaced (so you know what "ignore" hides)

- **`core/errwatch.py`** — the SOLE surface that includes global (`session_id=''`)
  rows: the scorebar `⚠ N` chip (`poll()` COUNT) and the mirror `⚠ audit:` /
  `⚠ audit: global:` one-liners (`err_ops`). This is the one to filter.
- The **dashboard** `⚠` badge (`core/sessionapi.error_count` / `errors`) is
  chain-scoped to a single session and deliberately EXCLUDES global rows, so
  `session_id=''` degrade-audits never show there — no change needed for those.
  (A real per-session swallowed exception shows in both; fix it, don't ignore.)

## 3. Ignore a benign signature

Only ignore an audit that is a **normal, expected return path** (§1, second
bullet). Add its `func` string to **`IGNORE_FUNCS` in `core/errwatch.py`**, with
a comment saying why it is benign and the investigation date:

```python
IGNORE_FUNCS = frozenset({
    "model_usage._slug_for",   # + a why-benign comment
    "<your.func>",             # why this is an expected outcome, date
})
```

This keeps the row **written and queryable** (`bin/claude-audit.py errors ''`)
but drops it from both the chip count and the painted one-liners. It does NOT
delete history and does NOT touch the call site — the audit stays intact for
future debugging; it just stops lighting up.

Prefer FIXING over ignoring. Reach for the ignore list only for a genuine
expected-outcome degrade-audit; never silence a real stack trace.

### Verify

```sh
python3 -m pytest tests/test_l0_units.py -k errwatch -q   # incl. the ignore test
```

The row still exists (`… errors ''` shows it); the chip/mirror no longer surface
it. `IGNORE_FUNCS` is process-scoped in each long-lived poller, so the scorebar
picks it up on its next re-exec; a running **dashboard** must be restarted only
if the change also touched a module it imports (this one is scorebar-side —
errwatch is not imported by the dashboard server, so no restart is needed for
the ignore to take effect there).

## 4. Empty the count after a FIX (handled ≠ gone)

**Handling an error does NOT empty the `errors` table.** The audit is
append-only, and the scorebar `⚠ N` chip is a COUNT of **all-time** rows
(`errwatch.poll`: `SELECT COUNT(*) … WHERE session_id IN (<sid>, '')`, minus
`IGNORE_FUNCS`) — not a count of rows *since* some checkpoint. So the two
dispositions clear the light differently:

- **IGNORE** (§3) clears the chip on its own — `IGNORE_FUNCS` is subtracted from
  the COUNT, so those rows stop counting the moment the poller re-execs. Nothing
  else to do.
- **FIX** does NOT clear the chip on its own — fixing the root cause only stops
  *new* rows; the rows already recorded stay in the table and keep counting. A
  **per-session** fixed signature ages out naturally (a fresh session's chip
  counts only its own sid plus global, so it starts at 0), but a **GLOBAL**
  (`session_id=''`) fixed signature keeps lighting ⚠ in *every* session forever
  until its rows are removed.

So after you FIX a **global** signature, delete its now-resolved rows to clear
the light — the sanctioned `sql-write` fixup path, surgical (by `func` under
`session_id=''`), never a blanket wipe:

```sh
python3 bin/claude-audit.py sql-write \
  "DELETE FROM errors WHERE session_id='' AND func IN ('<fixed.func>', …)"
# verify the non-ignored global count is now 0 (adjust the ignore list to match):
python3 bin/claude-audit.py sql \
  "SELECT COUNT(*) FROM errors WHERE session_id='' AND func NOT IN ('model_usage._slug_for')"
```

The chip re-polls every ~5s, so it drops without a restart. Delete ONLY
signatures you have **confirmed resolved** (their traceback is captured in your
investigation above) — never a live signature, and never the IGNORE_FUNCS rows
(those are kept on purpose for debuggability, §3). Don't delete per-session rows
to chase a number; they don't follow you into new sessions.

## 5. Wire-up rules (same-commit, per CLAUDE.md)

- Editing `IGNORE_FUNCS` is the whole change for an ignore — no new audit rows,
  no schema change. Update the comment so the "why benign" is on record.
- If instead you FIX a real error, follow the normal audit-coverage rules
  (`.claude/skills/audit-debug/SKILL.md`) and keep the `A.error` **call site**
  (so a recurrence re-audits) — the fix is proven by no NEW rows appearing, not
  by gutting the call. The already-recorded rows are separate: they don't vanish,
  and for a **global** signature you clear them per §4 to drop the count.
- A new benign signature that recurs across many sessions is a smell that the
  degrade-audit should have been an expected-outcome return in the first place;
  consider whether the call site should stop calling `A.error` at all.
