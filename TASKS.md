# Task list

The working queue. One owner (the lead session) updates it; done items move
to the bottom with their commit.

## In flight

(nothing running — the 4/4b/4c batch is DONE; open: the EnterPlanMode
ticket, and bug 8's hard-reload check by the owner)

Bug 8 could NOT be reproduced at HEAD: every expand path was verified in
a real Chromium, and a new test clicks a real block open and closed. The
report stays open until the owner retries after a hard reload; if it
returns, we need the session id, the entry type, and the browser console.
Audit follow-ups (owner ordered): (a) frontend tripwires — agent running
(feed.block.toggle.fail, feed.block.unbound, global onerror net);
(b) daemon path — fold-time check: a body-carrying entry type folded with
an empty body writes an audit errors row naming session, entry id, entry
type and source event. Route exceptions and reaction steps are ALREADY
audited (api/app.py:120, engine/react/loop.py); the empty-body shape is
the one gap. Starts when an agent slot frees.

## Owner decisions, recorded 2026-08-21

- EVERY wave ends with the e2e suites and their failures FIXED before the
  wave counts as done: `make test`, the kitty smoke test
  (CLAUDE_E2E_KITTY=1, marker `kitty`), and the live `make test-drift`.

- Order: bugs 5–8 first, then the 4/4b/4c batch.
- Item 4 extra fields: FAIL on any unknown field in a foreign record
  (strictest; a vendor update stops translation with `translation_failed`
  until the field is declared; rebuild heals history after the fix).
- Item 4b: `StrEnum`, so stored JSON and HTTP responses stay byte-identical.
- Item 4c: one typed public method per command; all methods go through one
  private core that writes the one audit row.

## Small tickets

(none)

## Done

- The 4/4b/4c batch, complete: loose-annotation gate (216 pinned, then
  emptied), codex + claude_code parse into declared models with
  extra=forbid (drift fixed against real corpora and live e2e),
  StrEnums for every vocabulary of ours with Gate 5, one typed method
  per command over one audit core. Suites green throughout; daemon
  current.

- HarnessName typed everywhere outside api/, Gate 4 with a zero-entry
  allowlist — the wave 2 commit.
- Codec dissolved, five words banned with a gate, one CanonicalEvent class
  end to end — `c85eec9`, `3339d38`.
- MigrateAccount removed, SCHEMA_VERSION 18, store rotated — `f2dd3f6`.
- Rename sweeps A+B (465→560 sites, both waves) — `3fddea7`, `37d2d37`.
- Typed-id sweep (15 new NewTypes, gate 2 green) — `19be80b`.

- Daemon slowness (fork storm, per-session `ls`, git dedup) — `c8059fc` and
  earlier.
- SSE fallback loop (`ActorResponse.session_id`) + frontend loud failures —
  in the restore checkpoint `eb6fcc9`.
- One model name everywhere + rebuild-heals-history — `276a28c`, `81043ab`.
- Typing migration: every production package strict — `c8059fc`, `3d27f6f`.
- Guard drop — `f468ecf`.
