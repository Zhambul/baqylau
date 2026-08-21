# Task list

The working queue. One owner (the lead session) updates it; done items move
to the bottom with their commit.

## In flight

1. **Rename sweep, wave A** — agent `rename-a` (sonnet): parameters named
   after their class in domain/audit/core/repository/engine/app (~205 sites).
   Gate: `tests/test_canonical_naming.py::test_a_parameter_is_named_after_its_class`.
2. **Rename sweep, wave B** — api/dashboard/notify/terminal/harness (~260
   sites). Starts when wave A lands.
3. **Typed-id sweep** — no bare `str` for `*_id` params/fields outside api/
   (161 sites). Gate: `test_an_id_is_a_typed_id_not_a_bare_str`. New NewTypes
   (WindowId, CallId, AccountId, …) go in domain/ids.py; reuse before create.

## Queued (approved direction, plan needs owner approval before implementation)

4. **Absolute type safety** — ban `object`, `Any`, `dict[str, Any]`
   annotations everywhere, NO foreign-document allowlist: the translators
   parse hook/transcript/rollout records into declared models and FAIL FAST
   on shape mismatch (a `translation_failed` verdict is the intended
   behavior). Major: plan first, owner approves, then sonnet agents per
   harness.
4b. **Enums, not string vocabularies** — every `frozenset({"toggle", ...})`
   command set and every `Literal["...", "..."]` union becomes an enum
   (`StrEnum`, so stored JSON and the wire stay byte-identical). Covers
   domain/values.py's type aliases (Outcome, ActorRole, MessagePhase,
   FileAction, PlanState, …), the pane COMMANDS set, and every other bare-str
   closed set. Enforce with a gate: no new `Literal` string unions, no
   module-level `frozenset` of strings used as a vocabulary. Part of the same
   plan-then-approve batch as item 4.

## Queued bugs (reported 2026-08-21, diagnose then fix; sonnet agents)

5. **Main dashboard shows ALL entries** — it must show only the LEAD actor's
   entries; the terminal app keeps showing all. Likely the feed scope default
   regressed in the rewrite/restore.
6. **Repeated "is done" notifications** — the same finished sessions notify
   over and over (eight pushes across five sessions in one stretch). Suspect:
   the notifier re-fires on daemon restarts or on status flaps; check
   `notification-route`/`notification-suppressed` audit rows and the
   retraction path.
7. **Burst of per-session /sessionData/{id} reads on page load** — diagnosed:
   the global stream opens with no `after_cursor`, so its first frame carries
   the whole backlog and can BEAT the list response; every session then looks
   unknown and `adoptStreamedSession` fetches each one. Fix: open the stream
   from the list's cursor (the list read and the stream share one high-water
   mark), and only adopt sessions the list has already answered for.
8. **Nothing expands on the web dashboard** (Update entries, shell entries) —
   reproduce with the console open; likely a JS regression from the restore.

## Awaiting owner decision

9. **Codec** — answered "why do we need it": the store needs the envelope
   declaration, write-time validation, the version gate, and
   discriminator-first decode; it is all pydantic already. Options: leave in
   domain/codec.py, or move into repository/mapper. No action until the owner
   picks.

## Done

- Daemon slowness (fork storm, per-session `ls`, git dedup) — `c8059fc` and
  earlier.
- SSE fallback loop (`ActorResponse.session_id`) + frontend loud failures —
  in the restore checkpoint `eb6fcc9`.
- One model name everywhere + rebuild-heals-history — `276a28c`, `81043ab`.
- Typing migration: every production package strict — `c8059fc`, `3d27f6f`.
- Guard drop — `f468ecf`.
