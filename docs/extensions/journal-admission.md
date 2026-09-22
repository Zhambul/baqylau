# Journal admission design decision

Status: in_progress

Owner: Codex

Date: 2026-09-15

Decision: The user approved the internal storage refactor on 2026-09-15. Store shared bodies once and use typed references in processing steps. Keep the worker protocol and extension package layout unchanged. The normalized storage boundary and pipeline admission are implemented. Bounded public reads and private-process acceptance remain open.

Related task: P04-T04. P04-T02, P04-T03, P04-T05, and P05 history reads also use this boundary.

Context: The journal currently stores complete worker requests and replies, followed by another copy of the final facts. Consecutive transforms can repeat the same inputs, settings, and prior snapshots. The 32 MiB proposal check runs only after the pipeline builds all steps. A valid reply below the transport limit can therefore leave its original permanently pending until the runtime or input changes.

Task: Replace repeated journal bodies with typed references to immutable stored bodies. Enforce admission before a call and before applying its result. Preserve the complete evidence for admitted processing and explicit evidence for work rejected by a limit. Keep original observations, stable fact identity, and the existing atomic acceptance boundary.

Outcomes: Proposed, not implemented. A large but admissible reply can publish without repeated body copies consuming its journal budget. A reply that does not fit cannot change the current candidates. Its rejection has an explicit size, digest, owner, stage, and reason. Repeated no-op transforms do not repeatedly store their input bodies. The worker protocol and extension package layout remain unchanged.

## Current reproduction

The reproduction used a temporary database, the actual pipeline and schema checks, and a controlled local canonical capability. It did not use a worker process or a live user database.

| Measurement | Observed result |
| --- | --- |
| Canonical additions | 20 |
| Document text | A JSON string with 1,048,574 ASCII `x` characters, plus its two quotes |
| Encoded typed reply | 20,984,583 bytes |
| Current transport frame limit | 33,554,432 bytes; this case did not send a frame |
| Current journal limit | 33,554,432 bytes |
| Result | `ValidationError`: interpretation proposal exceeds its encoded size limit |
| Canonical capability calls | 1 |
| Pending originals afterward | 1 |
| Accepted canonical head afterward | 0 |

The input was created through `tests.extension_host.processing_pipeline_fixture.installed()` in a temporary directory. The canonical reply used the valid insertion from `interpretation_transforms.insertion()`. Each insertion received a distinct `DerivedIdentity` key, `item-0` through `item-19`, and its corresponding `derived_event_id()`. Each document retained the declared schema, owner, scope, and cause. `PipelineCase.run()` reached the proposal byte check after the canonical result checks. No fact or interpretation was accepted.

This proves a pipeline admission defect. It does not prove worker transport, daemon behavior, core lifecycle failure, or the proposed fix. The temporary reproduction data was removed by its test context.

Current code areas: `extensions/models/interpretations.py`, `extensions/interpretation_pipeline.py`, `extensions/interpretation_calls.py`, `extensions/models/interpretation_steps.py`, and `repository/impl/sqlite/interpretation_writes.py`.

## Proposed storage boundary

The names in this section are proposed. Select the actual next schema version at implementation time. Worker shutdown observations now use schema 34; schema 33 was current during the original reproduction.

| Part | Proposed responsibility | Required rule |
| --- | --- | --- |
| Journal header | Bind original, history, runtime, codec version, verdict, and final fact references | One header identifies one complete interpretation |
| Immutable body storage | Store typed canonical bodies, raw content, decoder-state documents, and shared snapshots by content digest | Digest, kind, encoded size, and exact bytes must agree |
| Ordered typed steps | Store call selection, references, applied operations, and failure evidence | Every eligible call is accounted for in runtime order |
| Admission state | Count step metadata and new referenced body bytes before accepting more work | Reserve enough space for a complete failure record and required host work |
| Read adapter | Resolve typed references through a supported journal codec | No feature import, running worker, or mutable live setting is required |

Use explicit model codecs. Do not walk arbitrary JSON dictionaries and replace matching strings. A reference to a canonical fact is not a reference to an arbitrary document. Preserve the strict core branch, scope identity, schema ownership, and accepted metadata.

Store one immutable body version for each distinct checked value. Replacing a fact can create a new body version while retaining the same logical event ID. Do not overwrite the first accepted canonical body. Later proposals and suppressed intermediate facts must remain distinguishable.

References must retain their data for as long as a journal needs it. Do not replace copied data with references to mutable decoder-state rows or removable package files. Stored runtime and schema references must remain usable after disable, reload, restart, and package removal. Define reference ownership and cleanup before exposing deletion or history cleanup.

Original observation storage remains separate and immutable. Content digests establish byte identity, not authorization. Existing owner, scope, runtime, settings, history, cause, and lifecycle checks still apply before acceptance.

### Code boundaries confirmed after lifecycle integration

The following points define the next implementation work. They are not implemented storage features.

- Keep processing format and storage codec version separate. Format 1 means the old complete core pass. Format 2 requires the original lifecycle prefix and separate activity. A body-reference storage codec must not make an old complete pass valid for a new write.
- `InterpretationProposal.validate_proposal()` currently measures the expanded JSON. Normalized storage alone cannot fix the reproduced failure while this check still runs before the repository sees a proposal. Move exact write admission to the normalized journal boundary. Keep independent limits for expanded worker requests, final fact output, and full logical reads.
- `interpretation_steps` is currently a SQLite view over `json_each(interpretation_journals.proposal, '$.steps')`, not an owned step table. A migration must preserve old step reads and give new stored references an explicit read path. Do not make the existing view return references where a caller expects a complete worker step.
- Reuse the SDK's generic keep, drop, replace, and insert structures with typed internal fact or raw-input references. Use explicit request and reply codecs for raw transforms, extension translation, core activity, lifecycle, and canonical transforms. Do not encode all step types through an untyped document map.
- Store processing context, exact settings documents, prior-state snapshots, decoder-state documents, raw content, and each distinct fact body as immutable typed values. Prior accepted facts also need their original cursor and acceptance time. A later proposal with the same logical fact ID can require another body reference.
- Count the complete set of bodies used by this journal even when a body already exists in another journal. Otherwise admission depends on unrelated stored history and cannot be checked from one journal's declared ownership. Physical database deduplication and per-interpretation admission are different concerns.

The repository can recompute request admission from retained inputs and declarations. A reply which is not retained is different: its actual size and digest are a host observation at the receive boundary. Storage can check the owner, position, available budget, rejection rule, and unchanged output; it cannot reconstruct omitted bytes to prove the observed digest. State this limit in the model and diagnostics. Do not describe a non-retained reply as independently verified content or as a complete stored reply.

## Proposed admission behavior

Admission must run before the result changes the current candidates, not after the full proposal is assembled. Use the same exact size calculation in live processing and independent repository validation. Count UTF-8 bytes and structural metadata, not Python character counts or an approximate object size.

The budget must account for the complete accepted trace, remaining control records, and required host decoding. It must also limit expanded canonical output. Deduplicating a journal does not justify an arbitrarily large canonical write or transport request.

A rejected result keeps the preceding input unchanged. If retaining the complete rejected body would exceed the limit, record an explicit rejection summary with its encoded size and digest. Mark the body as not retained. Do not present this as a complete stored reply or silently truncate accepted evidence. This follows the existing distinction between a received typed reply and a failed transport call with no reply.

If another call cannot be admitted, record that it was not called. A compact limit record must identify its pipeline position and selected owner or exact remaining selection. The repository must be able to reject a false limit claim. Do not bypass the current coverage checks with an unchecked owner, claimed size, or digest.

Required core lifecycle work must not become optional because an extension consumes its budget. Prove the reservation and fallback rules before enabling budget-based omission on core input. The harness lifecycle split is approved. Its protocol, engine calls, and typed journal steps are implemented. Required input uses an original byte length and digest, not another worker content copy. Large-original preflight retains required facts. This does not yet reserve space against later journal growth. See the [lifecycle split record](lifecycle-split.md). Do not retry a stateful core decoder merely to recover a missing baseline or verify its output.

Do not clear pending input with a partial journal. Do not repeat an accepted interpretation. Source progress, original storage, and the retained runtime rules remain as implemented.

## Atomic writes and existing history

Publish the admitted header, ordered steps, body ownership links, decoder-state change, fact links, final facts, and pending removal in the existing complete interpretation transaction. A failure at any write or at COMMIT must preserve the previous logical database. No worker call belongs inside that transaction.

Keep existing schema-32 through schema-34 journal data readable. Prefer a versioned codec and a small migration over rewriting all old bodies during startup. If conversion is necessary, use the existing transactional migration runner and independent old-schema fixtures. Do not treat re-encoding as canonical reprocessing or change existing accepted facts.

Separate write admission from read expansion. A compact journal can expand beyond the old 32 MiB limit when returned as a full logical request/response object. Public diagnostics therefore need bounded metadata pages, step pages, and body reads. Do not remove the current read limit and return an unbounded response. P04-T05 must expose incomplete or paged reads explicitly.

## Verification required before completion

- The reproduced 20-addition case either fits the declared normalized budget and publishes once, or has a complete, explicit per-extension budget rejection with unchanged input. It must not repeatedly fail only because the same admitted bodies were copied into the journal.
- Multiple no-op transforms reuse their input and prior-state evidence. They retain exact order and settings identity. Generated data still moves only to later transforms.
- A later oversized reply preserves all earlier accepted decisions, rejects the entire new result, and records its actual size, digest, and retention status.
- Request admission failure records no worker invocation. Storage rejects a false budget claim, wrong owner, changed input, missing body, wrong digest, wrong body kind, missing eligible step, and stale runtime or state.
- Empty output, all-dropped output, duplicate logical facts, and exact retries preserve their current verdict and first-acceptance rules.
- Core start, finish, cleanup, and strict fact identities remain valid under small budgets. A limit must not hide required core work.
- Every body/header/step/link write and actual COMMIT failure has a complete rollback test. No unowned body is published after a failed acceptance.
- Old inline journals remain readable after migration and after worker or package removal. Separate history revisions cannot share mutable state.
- Public reads have explicit page and byte bounds. A large expanded trace cannot cause an unbounded response or an empty-page progress loop.
- Run the case through at least two actual external workers and the private daemon. Verify through public diagnostics when P04-T05 supplies them. Local capability mocks are not worker or package-owned E2E evidence.
- Run the shared type, Ruff, Wemake, dead-code, architecture, and regression checks. Do not add exemptions to hide missing callers or weakened validation.

Status result: The normalized storage boundary is implemented. Pipeline admission before calls and result application, bounded public reads, and private-process acceptance remain open. P04-T04 is still in progress. No phase or subtask is complete from this investigation. All P01–P10 work, including web, Kitty, adapters, Git, and package-owned E2E, remains in scope.

## Implementation record — normalized journal storage

Date: 2026-09-19

Owner: Claude Code

Status: in_progress

Context: The reproduced 20-addition case failed the expanded proposal byte check after one valid canonical reply. The same facts appeared in the reply, later requests, and the final fact list. The approved decision stores shared bodies once and refers to them from ordered steps.

Task: Implement the normalized storage boundary without changing the worker protocol or the package layout. Keep codec version 1 rows readable and exactly retryable. Admit exact write size at the normalized boundary.

Outcomes:

- Schema 35 adds `interpretation_bodies`, `interpretation_journal_steps`, and `interpretation_journal_bodies`. `interpretation_journals` gains `codec_version` with default 1. Migration 32 keeps its original table shape so migration 35 adds the column to every older database.
- `extensions/models/interpretation_bodies.py` defines `BodyRef` (kind, SHA-256 digest, exact encoded length) and `BodyStore`. The store interns each distinct value once, checks digest and length agreement, and counts each distinct body byte once.
- `extensions/models/interpretation_storage_steps.py` defines the stored step models. Every fact, raw input, content bundle, prior snapshot, decoder state, and encoded document becomes a typed reference. Requests, replies, operations, decisions, diagnostics, and metadata stay inline.
- `extensions/models/interpretation_normalization.py` converts one logical proposal to a `NormalizedJournal` and back. `normalized_byte_length()` counts the header, every step document, and every distinct body.
- The write transaction normalizes the proposal, checks the normalized size against the 32 MiB journal limit, writes each body once with its ownership link, and writes each ordered step with its stage. A rejected journal writes no fact, body, or step, and keeps the original pending.
- The read path branches on `codec_version`. Codec 1 rows keep the inline proposal and the `interpretation_steps` view. Codec 2 rows store the header in `proposal` (no `steps` key) and expand the owned steps through the referenced bodies. Exact retries compare the expanded logical proposal.
- `InterpretationProposal.validate_proposal()` no longer measures the expanded JSON. Exact write admission moved to the normalized journal boundary.
- `JournalAdmission` tracks the normalized size while the pipeline runs. It checks the call budget before every extension worker call and admits each normalized step before its result changes the candidates. A 1 MiB reserve holds the journal header, the final fact references, and one control record.
- A call that the budget does not allow becomes a `LimitStep` with its stage, owner, and exact selected input IDs. The worker is not invoked. A reply that does not fit becomes a `RejectedStep` with its exact encoded byte length and SHA-256 digest. The reply body is not retained, and the preceding input stays unchanged.
- Required core lifecycle and activity steps are never optional. They enter the journal without a limit decision; if the complete journal then exceeds the limit, the write fails and the original stays pending.
- `validate_admission()` replays every step in the write transaction. It rejects a limit record while the call budget still has room and rejects a rejection record whose observed size the remaining budget could hold.

Evidence: `tests/extension_host/test_interpretation_normalization.py` passes four cases: the reproduced 20-addition reply (expanded JSON above the limit, normalized journal below it, one accepted fact set, exact read-back), one stored body version for a repeated fact, a rejected normalized journal with pending input and no rows, and the codec-2 read path. `tests/extension_host/test_interpretation_admission.py` passes five cases: an exhausted budget records two limit steps and invokes no worker, an oversized canonical reply keeps the earlier facts and records its exact size and digest, a false limit claim and a false rejection claim are rejected, and a small budget still publishes the required core lifecycle facts. The legacy journal cases, the migration and rollback cases, and the complete extension host selection pass sequentially: 905 cases pass with no failure. The module split moved the journal write behind `interpretation_journal_writes.write_journal`; the schema-32 upgrade fixture now patches that seam, and its four migration cases pass. Strict types and root Ruff pass for the changed files. The `tests/` selection excluding the extension host passes 2,409 tests with 14 warnings in 156.19 seconds, excluding Kitty and live `tests/e2e`. Five frontend-build environment cases fail because the worktree has no installed web dependencies or built bundle; they do not touch the changed code. The full Wemake gate now passes with zero findings: the stored-step codec is split into staged modules (context, operations, outcomes, requests, results, core, models, and per-stage store/load codecs behind one facade), and the audit read is split into source detection, query building, row mapping, and builder modules. The full-tree Ruff gate and strict types pass for 2,861 files.

Limits: A reply still becomes a typed in-memory object before admission rejects it; the transport limit is the bound on that copy. Storage cannot reconstruct a non-retained reply, so the rejection check uses the recorded size against the remaining budget, not the omitted bytes. Public reads have no new page or byte bound; P04-T05 owns that. The cases use controlled local capabilities, not external worker processes.

Next action: Add bounded public reads and the private-process drop, replace, insert, rollback, and restart cases.
