---
name: global-errors
description: Investigate recent audit `errors` rows whose `session_id` is empty, reproduce active failures, and fix their root causes. Use when asked to inspect global or recent application errors.
---

# global-errors — triage cross-session failures from current evidence

The audit database records exceptions that application boundaries deliberately
swallow so the daemon, hooks, streams, and notifications can continue running.
Rows with `session_id=''` are cross-session: the failure happened before a
session could be identified, or in machinery shared by every session.

These rows are diagnostic history. They are **not** currently included in the
dashboard's per-session warning counts: `SqliteAuditReadRepository.error_counts`
explicitly excludes the empty session id. There is no `core/errwatch.py`, no
`IGNORE_FUNCS`, and no global warning-light allowlist in the current tree.

The goal is to distinguish historical failures from active ones, reproduce the
active failures, and fix their causes without hiding or deleting evidence.

## 1. Read the audit database safely

Run from the repository root. Resolve the data path through `core/data.py` when
`BAQYLAU_DATA_DIR` is configured; the normal path is shown below. Triage is
read-only.

```sh
audit_path=/Users/z.yermagambet/.local/share/baqylau/audit.db

# Global signatures, newest first.
sqlite3 -readonly -header -column "$audit_path" \
  "SELECT script,func,COUNT(*) n,MAX(datetime(ts,'unixepoch','localtime')) last
   FROM errors WHERE session_id=''
   GROUP BY script,func ORDER BY last DESC"

# All signatures, including session-scoped failures.
sqlite3 -readonly -header -column "$audit_path" \
  "SELECT script,func,COUNT(*) n,MAX(datetime(ts,'unixepoch','localtime')) last
   FROM errors GROUP BY script,func ORDER BY last DESC,n DESC"

# Full recent evidence for one signature.
sqlite3 -readonly -line "$audit_path" \
  "SELECT id,datetime(ts,'unixepoch','localtime') time,session_id,traceback,context,pid
   FROM errors WHERE func='<func>' ORDER BY ts DESC LIMIT 10"
```

`bin/claude-audit.py` was deleted in the canonical rewrite; do not recommend it.
`bin/baqylau-raw-events-audit.py` is for the main database's raw-event spine,
not operational `errors` rows.

## 2. Decide whether a signature is still active

Counts are all-time and can be enormous after a tight retry loop. Always compare
the newest timestamp and writer PID with the running daemon:

```sh
pgrep -af 'bin/baqylau-dashboard.py serve'
sqlite3 -readonly -header -column "$audit_path" \
  "SELECT func,COUNT(*) n,MAX(datetime(ts,'unixepoch','localtime')) last
   FROM errors WHERE pid=<current-pid> GROUP BY func ORDER BY last DESC"
```

- A signature produced by the current PID is active.
- A signature ending before the current process started may already have been
  fixed or may require the old trigger. Reproduce it before editing code.
- A rapidly increasing count means a retry loop. Inspect the newest full
  traceback plus its context before changing code.
- `NoneType: None` means `AuditRecorder.error` was called outside an `except`
  block. Read the call site to decide whether it records a real degraded outcome
  or whether the caller is incorrectly auditing normal control flow.

Find the call site with `rg -n '"<func>"' api app audit engine harness notify
terminal client`. The `func` column is the stable signature; `script` only names
the process that recorded it.

## 3. Reproduce and fix

For session-related errors, continue with `audit-debug`. For global failures:

1. Reproduce against an isolated test database or a SQLite `.backup`, never by
   mutating the live store.
2. Add a regression test at the boundary that swallowed the exception.
3. Fix the originating invariant or adapter mapping; keep the audit call so a
   recurrence remains observable.
4. Run the focused test, architecture tests, lint/type checking, and the normal
   suite in proportion to the change.
5. Restart the daemon if it imports changed code, then query errors for the new
   PID and confirm the signature does not recur.

Do not delete historical audit rows merely to reduce a number. They no longer
pollute per-session warning counts, and they are the evidence needed to prove
when a defect began and stopped. If the user explicitly requests retention
cleanup, back up `audit.db` first and make the deletion surgical by signature and
time range.

## 4. Report

For every recent signature state:

- latest timestamp, count, and whether the current PID produced it;
- the exception and relevant context;
- whether it was reproduced, already fixed, or remains actionable;
- the code path and regression test for any fix.

Do not describe old rows as active errors just because they remain in the
append-only table.
