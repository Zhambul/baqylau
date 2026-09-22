# Required session lifecycle and extension activity

Status: in_progress

Owner: Codex

Date: 2026-09-15

Context: One raw input can start a session and report activity. A raw transform must be able to change, drop, or add that activity. It must not remove the session start or prevent cleanup after a session finish. The user approved this protocol change.

Task: Separate required processing of the original input from processing of extension-controlled activity. Keep both results in the complete interpretation transaction. Preserve required facts through raw and canonical transforms.

Outcomes: The host retains required session state even when all activity is dropped. Changed or added activity cannot start or finish a session. Cleanup follows newly accepted required facts, not worker replies.

Verification: Run the protocol, journal, storage, and private-process checks below. Do not enable core raw changes until all three parts are connected.

## Protocol implementation

Status: in_progress

Context: Codex and Claude Code keep tool, turn, and compaction state in memory. Calling their complete translator twice would change that state twice. Filtering a complete translation after a raw transform would lose required state when the input is dropped.

Task: Give `HarnessTranslator.translate` and `CoreTranslator.translate` an explicit, keyword-only `translation_stage: TranslationStage` parameter. Keep the existing single pass available while the journal is changed.

Outcomes:

- `COMPLETE` is the default. Existing callers retain the single-pass behavior.
- `LIFECYCLE` receives the original input before extension calls. It does not process tool, turn, or compaction activity.
- `ACTIVITY` receives each surviving or added input. It produces no required session facts.
- Required facts are `SessionStarted`, the lead `ActorStarted`, and `SessionFinished`. Child actor activity, turn events, title changes, and account changes remain activity.
- Claude Code has a separate native lifecycle reader. It uses the existing session builders without calling the tool, turn, or selection handlers. Its activity reader excludes required facts from its result.
- Codex selects the pass before its stateful record handlers. The lifecycle pass can set the original working directory and source ownership. The activity pass does not call the session-start helpers or change the working directory through added session metadata.
- OpenCode2 reads start fields separately from activity payloads. The core service translators have no translation memory and select the requested fact group.
- `release_session` remains a separate host call. A lifecycle read does not release memory before the transaction succeeds.

Code areas: `harness/contracts/events.py`, `harness/models/translation_stages.py`, the three harness translators, `harness/impl/claude_code/canonical/required_translation.py`, and the core translators in `engine/interpret`.

Verification: The 17 new protocol cases pass. They cover a dropped Claude prompt with unchanged turn memory, changed prompt content, added session finish suppression, explicit release, Codex compaction memory, added session metadata, four core start/finish inputs, a core interrupt, and four saved OpenCode2 streams. The existing complete-pass tests remain required. These are host protocol tests, not external package E2E tests.

Evidence: `tests/plugin_tests/test_translation_stage_claude.py`, `test_translation_stage_codex.py`, `test_translation_stage_core.py`, `test_translation_stage_opencode.py`, and `translation_stage_fixture.py`. No extension worker wire format or package layout changed. The protocol-only work retained `COMPLETE` in the engine. The later integration below now uses both separate passes. Core raw changes were enabled after the private-process acceptance below.

Initial full run: 3,274 tests passed and two repository policy tests failed. The new parameter used the name `stage`, and the Claude lifecycle reader declared a module-level string set. The parameter now uses the required class-based name. The source filter is local to its dispatch. After these corrections, all 22 focused protocol and naming checks pass. No rule or exemption changed.

Six-worker repeat: 3,275 tests passed and the existing `test_workers_follow_declared_order[owners0]` reached the global 30-second test limit during its second daemon shutdown. Its first-run event-order checks had passed. The same case then passed alone in 21.73 seconds, including 19.29 seconds in the test call and 2.25 seconds in setup. A later process scan found no command containing that failed private case path. This does not prove a fixed six-worker time bound. Keep this test reliability issue open; no timeout or assertion was changed.

Final verification: The complete four-worker run passed 3,276 tests with 18 warnings in 330.69 seconds. Command: `PATH=/Library/Developer/CommandLineTools/usr/bin:$PATH .venv/bin/python -m pytest -q -m 'not kitty' -n 4 --ignore=tests/e2e`. This includes the original exited-worker shutdown regression and all new protocol cases. Strict types pass for 2,814 files. Root Ruff, changed-file Wemake, shared policy parity, and the plan checks pass. The same six unrelated Wemake findings and 28 prior dead-code findings remain. No live daemon, user database, external service, or Git remote was changed. This verifies the protocol subset; journal and engine integration remain open.

## Journal and transaction integration

Status: in_progress

Context: Before this change, the journal started with raw transforms and translated only their surviving output. It could not record required processing when that output was empty. Its core step represented the old complete pass. Old stored journals must remain readable.

Task:

1. Add an explicit typed required-lifecycle step tied to the original input. Record it once before any raw transform. Do not consume the activity input when recording this step.
2. Record activity translation separately for every surviving input, in order. Require the activity stage at the harness boundary. Extension-owned originals continue to use their declared extension decoder.
3. Update independent trace validation. New core journals must contain exactly one required step. Reject missing, repeated, late, or mismatched required steps and activity steps with required facts. Preserve the old complete-step reader through an explicit format version.
4. Preserve the full required fact bodies and their order. Canonical transforms can change activity, but cannot change required payloads, identities, scope, or order. Do not rely only on the current start/finish ID check.
5. Retain the complete acceptance transaction, first accepted bodies, original links, and failed-reply records. Run core input reactions and session release only for newly accepted core facts after commit.
6. Cover admission failure and large input. The approved [shared-body journal design](journal-admission.md) must reserve space for required processing and a failure record. A dropped raw result or a journal limit must not silently omit the required pass.
7. [Done 2026-09-20] Remove the blanket rejection of core raw changes only after these checks pass.

Outcomes: Required lifecycle and changed activity share one checked transaction. All-dropped activity still permits required state and cleanup. An added raw input cannot create an additional lifecycle pass.

Code areas: `extensions/interpretation_pipeline.py`, `extensions/interpretation_contract.py`, `extensions/models/interpretation_steps.py`, trace and transform checks, `engine/interpret/translation.py`, and interpretation storage and codecs.

Verification: Direct storage tests reject omitted and forged steps. Rollback keeps the original pending and applies no input reaction. Retry preserves first acceptance and does not repeat cleanup. Old schema-32 through schema-34 journals retain their content and read behavior. Required facts survive raw drop, raw replacement, raw insertion, canonical drop, failed workers, and size limits.

Evidence: The engine now calls `translate_lifecycle` once for each original core input before worker selection. New `CoreLifecycleStep` records the original byte count and SHA-256 digest, decoder version, decision, and required facts. It does not construct a worker `ContentBundle`. Every surviving core input uses `CoreActivityStep`. Canonical transforms receive only activity. Required starts precede activity in final acceptance; required finishes follow it so cleanup runs last.

Format: `InterpretationProposal.format_version` is 2 for new writes. Absent versions decode as the old format 1. `CoreTranslationStep` remains available for old reads, but cannot enter a new trace. Exact stored retries are checked before new-write validation. Old-format size checks exclude the new default field, so the field cannot make an old stored journal exceed its former byte limit. No SQL schema change is part of this integration; the main schema remains 34.

Storage checks: New core journals require exactly one lifecycle prefix. Independent validation checks its original byte reference, version, required fact group, verdict, final bodies, and relative processing boundary. Missing, repeated, late, or changed steps fail before any write. Applied activity replies cannot create required lifecycle facts or use a required ID. Required facts have the same scope, source-reference, cause, and first-acceptance checks as other accepted facts. Storage does not run a stateful native decoder again to check the journal.

Large input: The pipeline runs the required pass before the worker content preflight. A valid large first prompt retains its session and lead actor starts. A valid large finish retains its finish and runs memory release after acceptance. Activity has an explicit unavailable step. Invalid native bytes retain a failed required decision and an unavailable activity decision. All original bytes remain stored.

Verification: The focused processing and storage selection passes 166 tests. New checks cover missing and forged steps, changed or dropped final required bodies, a first native prompt, a wrong harness activity fact group, large valid and invalid originals, three write failures, an actual COMMIT failure and retry, old core and extension journal reads, old exact retries, and the old byte-limit boundary. Strict types pass for 2,825 files. Root Ruff passes.

Initial full run: The four-worker run had 3,298 passes and three failures in 388.13 seconds. Two architecture checks found a new raw dictionary and an engine import from the broad `extensions.models` package. Required fact selection now uses a list and an ID set. The engine imports only the approved specific models; its existing mapping module builds the original-byte lifecycle reference. All 30 focused architecture, naming, and lifecycle checks pass after these fixes. No architecture exemption changed. The third failure was `test_workers_follow_declared_order[owners0]`, which reached the global 30-second limit during its second daemon shutdown. Its event-order assertions passed. Final regression verification remains in progress.

Restart check: The failed case passed alone in 24.47 seconds: 21.46 seconds in the test call and 2.64 seconds in setup. Read-only inspection of its failed-run private database found four shutdown observations. The first manager recorded successful resource closure with no issues. The second manager recorded no active runtimes in either shutdown observation. No process command from that full-run private root remained. The second test start reads stored journals and can stop before background runtime restoration completes; the records do not prove that preparation and total application shutdown fit the test deadline. Keep the concurrent restart timing issue open. No test limit or assertion changed.

Limits: Shared-body journal storage and per-call admission are now implemented. A later valid reply can still exceed the final journal size limit only through its exact normalized size, which the write checks before any row. These changes do not make stateful activity translation transactional across a failed database commit. Private-process drop, replacement, insertion, and restart acceptance is complete. The core raw guard was removed on 2026-09-20 after the raw acceptance cases passed. Core mapping failures and invalid core fact checks record explicit failed steps and keep the preceding input; they no longer block the whole interpretation.

## Private-process acceptance

Status: done

Context: Protocol tests do not prove that the daemon runs the required pass before worker calls or releases session resources after a checked write.

Task: Add external transform fixtures to the existing private-daemon tests. Use public activation, a private database, native-shaped input, and actual worker processes. Do not use a live user session.

Outcomes: The daemon can accept changed core activity while required session state remains under host control.

Verification:

- Drop activity from a raw input which also starts a session. Verify the required session and lead actor facts, complete journal, pending progress, and absence of dropped activity.
- Replace and add activity. Verify original start fields, only selected activity, forward-only transform order, and no generated lifecycle facts.
- Drop activity from a finish input. Verify accepted finish, source and translator release, later input processing, and normal daemon shutdown.
- Fail a worker and fail the acceptance transaction. Verify unchanged required facts, explicit failures, safe retry, and no early cleanup.
- Restart the private daemon. Verify stored facts, original bytes, journal version, and no repeated acceptance or cleanup.

Evidence: `tests/extension_host/test_lifecycle_daemon.py` passes six cases through the actual daemon and an external worker process built from `tests/extension_api/lifecycle_canonical_example.py`. The package owner selects the worker behavior; the fixture activates it through the public lifecycle API and delivers native Claude Code hook bytes through `POST /api/harnesses/claude_code/hooks`.

- Drop: one Stop hook starts the session and carries a turn. The journal records `core_lifecycle`, `core_activity`, and one canonical drop step. The accepted core facts are exactly `session.started` and `actor.started`; no activity fact remains, no extension document is accepted, and no input stays pending.
- Replace and add: the canonical step replaces the `turn.finished` outcome and inserts one owned fact with its exact document. The required start is unchanged and the added fact is accepted after the replacement.
- Finish: a SessionEnd input records `session.finished` with no canonical step, because the finish input has no activity left after the required selection. A later SessionStart for a second session is accepted, which proves the daemon released the first session and kept processing.
- Failed worker: the worker raises. The canonical step records an explicit failure with no reply. The required start and the unchanged activity remain accepted, the input is consumed once, and a repeated identical delivery adds no journal and no fact.
- Invalid reply: the worker returns an addition with a missing cause. The pipeline rejects the whole reply, records a failed step which retains the typed reply, and keeps the preceding input. The accepted core facts are unchanged.
- Restart: after the daemon stops and starts again, the stored journal and the accepted facts are unchanged and no input is pending.

`tests/extension_host/test_lifecycle_daemon_raw.py` adds three raw cases through the same daemon and worker path. Before the guard removal, the drop case failed: the rejected raw change blocked the whole interpretation, so no required fact was accepted.

- Raw drop: the selected raw input is dropped. The journal records `core_lifecycle` and `raw` only. The accepted facts are exactly the required session and actor facts, and the stored payload is the delivered hook byte for byte.
- Raw replace: the reply replaces the translation content. The recorded replacement content identity differs from the request's original identity, the activity still translates, and the stored payload is unchanged.
- Raw insert: the reply adds a derived input after its anchor. The journal records two activity steps, one for the original input and one for the inserted input, and the stored payload is unchanged.

The guard removal (`extensions/models/interpretation_transforms.py`) is safe because the host translates the required facts from the stored original before any worker call. A raw change can only alter what the activity pass reads afterwards.

The invalid reply case covers the pipeline acceptance check, which is the same check storage re-runs. A worker reply cannot fail storage alone. The local rollback cases in `test_lifecycle_pipeline_transactions.py` cover a failed commit, a failed statement, and a safe retry inside the transaction.

The complete extension host selection passes 919 cases sequentially in 646.85 seconds. The first full run exposed one fixture defect: the backend provided a raw transformer for canonical packages which did not declare that capability, so activation failed with `preparation_failed`. The factory now provides it only for the raw owners, and the six canonical cases pass.

The cases use public activation, a private database, native-shaped input, and actual worker processes. No live user session was used.
