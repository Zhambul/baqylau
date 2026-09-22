# Copyright (c) 2026 Zhambyl Yermagambet
"""Split Codex canonical translation."""

from __future__ import annotations

import os
import typing

from harness.impl.codex.canonical import (
    translator_batch_results,
    translator_dependencies as dependencies,
    translator_lifecycle_event_dependencies as event_dependencies,
    translator_lifecycle_runtime_dependencies as runtime_dependencies,
)
from harness.models import translation_stages as stages

if typing.TYPE_CHECKING:
    from harness.impl.codex.canonical.translator_lifecycle_protocols import (
        RecordTailTranslator,
        RecordTranslator,
        ShellResultTranslator,
    )

MALFORMED_ROLLOUT_RECORD = "malformed Codex rollout record"
MISSING_GOAL_OBJECTIVE = "Codex goal has no objective"


class _CodexTranslationLifecycle(dependencies.translator_service_dependencies.HarnessTranslator):
    """Represent codex canonical translator."""

    def __init__(
        self,
        rewind_continuity: dependencies.translator_codex_dependencies.RewindContinuity | None = None,
    ) -> None:
        """Initialize the object."""
        self._collaboration_calls: dict[
            tuple[str, str],
            tuple[
                str,
                dependencies.record_payload_namespaces.record_collaboration_registry.CollaborationArguments,
            ],
        ] = {}
        self._process_shells: dict[tuple[str, str], dependencies.translator_type_dependencies.ids.ShellId] = {}
        self._continuation_shells: dict[tuple[str, str], dependencies.translator_type_dependencies.ids.ShellId] = {}
        self._finished_shells: set[runtime_dependencies.translator_identity.SourceShellKey] = set()
        self._finished_shell_outcomes: set[runtime_dependencies.translator_identity.FinishedShellKey] = set()
        self._finished_skills: set[runtime_dependencies.translator_identity.SourceSkillKey] = set()
        # Announced background once. An exec that outlived its yield is reported
        # again by every continuation poll, and the fact is about the command,
        # not about the poll that observed it.
        self._backgrounded_shells: set[runtime_dependencies.translator_identity.SourceShellKey] = set()
        self._semantic_tool_calls: set[runtime_dependencies.translator_identity.SourceCallKey] = set()
        self._call_records: dict[
            tuple[str, str],
            dependencies.record_canonical_namespaces.record_tool_records.ExecRecord
            | dependencies.record_canonical_namespaces.record_tool_records.ToolRecord
            | dependencies.record_canonical_namespaces.record_actor_records.ToolBatchRecord
            | dependencies.record_canonical_namespaces.record_interaction_records.AskRecord
            | None,
        ] = {}
        self._mcp_tool_outcomes: dict[tuple[str, str], dependencies.translator_domain_values.outcomes.Outcome] = {}
        self._finished_tool_calls: set[runtime_dependencies.translator_identity.SourceCallKey] = set()
        self._plan_tasks: dict[
            tuple[str, str],
            tuple[dependencies.translator_domain_values.event_work.TaskChanged, ...],
        ] = {}
        self._goals: dict[str, dependencies.translator_domain_values.event_work.GoalChanged] = {}
        self._working_directories: dict[str, str] = {}
        # Codex's `turn_aborted` payload may omit `turn_id`.  One rollout file
        # carries at most one active turn, so retain the task-start identity by
        # source and use it to close that same turn when the terminal interrupt
        # record is sparse.
        self._active_turns: dict[str, dependencies.translator_id_dependencies.ids_conversation_types.CodexTurnId] = {}
        self._compactions: dict[
            tuple[dependencies.translator_type_dependencies.ids.SessionId, str],
            tuple[int | None, int | None],
        ] = {}
        self._sources_by_session: dict[dependencies.translator_type_dependencies.ids.SessionId, set[str]] = {}
        self._selections = dependencies.translator_service_dependencies.SelectionSemantics()
        self._rewind_continuity = rewind_continuity or dependencies.translator_codex_dependencies.RewindContinuity()

    def release_session(self, session_id: dependencies.translator_type_dependencies.ids.SessionId) -> None:
        """Release all transient correlation for one finished session."""
        sources = self._sources_by_session.pop(session_id, set())
        self._release_shell_state(sources)
        self._release_tool_state(sources)
        self._release_session_state(session_id, sources)

    def translate(
        self,
        raw_event: dependencies.translator_service_dependencies.raw_events.RawEvent,
        *, translation_stage: stages.TranslationStage = stages.TranslationStage.COMPLETE,
    ) -> dependencies.translator_service_dependencies.raw_events.TranslationResult:
        """Translate translate.

        Returns:
            The translation result.

        """
        self._sources_by_session.setdefault(raw_event.session_id, set()).add(
            event_dependencies.translator_started_events.source_key(raw_event),
        )
        try:
            translated = typing.cast("_CodexSourceTranslator", self)._translate(raw_event, translation_stage)  # noqa: SLF001 -- The cast still refers to self.
        except dependencies.translator_service_dependencies.raw_events.UnknownRawEventError as unknown:
            return dependencies.translator_service_dependencies.raw_events.TranslationResult(
                (),
                dependencies.translator_domain_values.records.RecordedTranslationDecision.IGNORED_UNKNOWN,
                unknown.reason,
            )
        return stages.select_result(translated, translation_stage)

    def _continued_from(
        self,
        raw_event: dependencies.translator_service_dependencies.raw_events.RawEvent,
        declared_from: str | None,
    ) -> dependencies.translator_type_dependencies.ids.SessionId | None:
        return self._rewind_continuity.resolve(
            raw_event.session_id,
            raw_event.terminal_window_id,
            declared_from=(
                None
                if declared_from is None
                else dependencies.translator_id_dependencies.ids_session.session_id_from_codex(
                    dependencies.translator_id_dependencies.ids_session_types.CodexSessionId(declared_from),
                )
            ),
        )

    def _release_shell_state(self, sources: set[str]) -> None:
        """Release shell correlation for source files."""
        runtime_dependencies.translator_indexes.drop_source_keys(self._process_shells, sources)
        runtime_dependencies.translator_indexes.drop_source_keys(self._continuation_shells, sources)
        self._finished_shells = {key for key in self._finished_shells if key.source_key not in sources}
        self._finished_shell_outcomes = {key for key in self._finished_shell_outcomes if key.source_key not in sources}
        self._backgrounded_shells = {key for key in self._backgrounded_shells if key.source_key not in sources}

    def _release_tool_state(self, sources: set[str]) -> None:
        """Release tool correlation for source files."""
        runtime_dependencies.translator_indexes.drop_source_keys(self._collaboration_calls, sources)
        self._finished_skills = {key for key in self._finished_skills if key.source_key not in sources}
        self._semantic_tool_calls = {key for key in self._semantic_tool_calls if key.source_key not in sources}
        runtime_dependencies.translator_indexes.drop_source_keys(self._call_records, sources)
        runtime_dependencies.translator_indexes.drop_source_keys(self._mcp_tool_outcomes, sources)
        self._finished_tool_calls = {key for key in self._finished_tool_calls if key.source_key not in sources}
        runtime_dependencies.translator_indexes.drop_source_keys(self._plan_tasks, sources)

    def _release_session_state(
        self,
        session_id: dependencies.translator_type_dependencies.ids.SessionId,
        sources: set[str],
    ) -> None:
        """Release state keyed by a session or its source files."""
        for source in sources:
            self._active_turns.pop(source, None)
        self._goals.pop(str(session_id), None)
        self._working_directories.pop(str(session_id), None)
        for key in tuple(self._compactions):
            if key[0] == session_id:
                self._compactions.pop(key, None)
        self._selections.release_session(session_id)
        self._rewind_continuity.release(session_id)


class _CodexPendingShells(_CodexTranslationLifecycle):
    """Track pending shell and tool call state."""

    def _only_pending_exec_shell(self, source_key: str) -> dependencies.translator_type_dependencies.ids.ShellId | None:
        """Return the only pending exec shell.

        The shell belonging to a fast CommandExecution item.

                Current Codex emits the authoritative item (with its real exit code)
                before the wrapper output. The item names a process id but not the
                wrapper call id; when exactly one exec is awaiting its result, that
                ordering is the correlation. Ambiguity stays uninterpreted rather than
                attaching an outcome to the wrong command.

        Returns:
            Only pending exec shell.

        """
        candidates = [
            dependencies.translator_id_dependencies.ids_session.shell_id_from_codex_call(
                dependencies.translator_id_dependencies.ids_session_types.CodexCallId(call_id),
            )
            for (known_source, call_id), record in self._call_records.items()
            if known_source == source_key
            and isinstance(record, dependencies.record_canonical_namespaces.record_tool_records.ExecRecord)
            and runtime_dependencies.translator_tool_paths.read_skill_name(record.cmd) is None
            and runtime_dependencies.translator_identity.SourceShellKey(
                source_key,
                dependencies.translator_id_dependencies.ids_session.shell_id_from_codex_call(
                    dependencies.translator_id_dependencies.ids_session_types.CodexCallId(call_id),
                ),
            )
            not in self._finished_shells
        ]
        return candidates[0] if len(candidates) == 1 else None

    def _pending_exec_shell_for_command(
        self,
        raw_event: dependencies.translator_service_dependencies.raw_events.RawEvent,
        native_command: tuple[str, ...],
    ) -> dependencies.translator_type_dependencies.ids.ShellId | None:
        """Match a completed native process to its wrapper command.

        Current Codex does not put the wrapper call id on a CommandExecution
        item. It does put the exact shell command there. This identity remains
        usable when a JavaScript cell starts several commands in parallel,
        where the old "only pending command" fallback is ambiguous.

        Returns:
            The shell id.

        """
        if not native_command:
            return None
        source_key = event_dependencies.translator_started_events.source_key(raw_event)
        command_texts = runtime_dependencies.translator_recovery.command_texts(native_command)
        candidates = self._pending_exec_candidates(source_key, command_texts)
        # Equal commands have equal meaning here. Native completions arrive in
        # start order, so the oldest open wrapper is the stable owner.
        if candidates:
            return candidates[0]

        # The call is durable in the rollout even when an application restart
        # cleared translator memory. Rebuild the pending set up to, but not
        # including, this completion record.
        end_position = runtime_dependencies.translator_tool_paths.source_position(raw_event.source_position)
        if end_position is None:
            return None
        recovered_call = event_dependencies.translator_selection_events.recover_pending_exec(
            source_key, end_position, command_texts,
        )
        if recovered_call is None:
            return None
        self._call_records[source_key, recovered_call.call_id] = recovered_call
        return dependencies.translator_id_dependencies.ids_session.shell_id_from_codex_call(recovered_call.call_id)

    def _pending_exec_candidates(
        self,
        source_key: str,
        command_texts: set[str],
    ) -> list[dependencies.translator_type_dependencies.ids.ShellId]:
        candidates = []
        for (known_source, native_call_id), call_record in self._call_records.items():
            if not runtime_dependencies.translator_indexes.is_pending_exec_candidate(
                known_source, call_record, source_key, command_texts,
            ):
                continue
            shell_id = dependencies.translator_id_dependencies.ids_session.shell_id_from_codex_call(
                dependencies.translator_id_dependencies.ids_session_types.CodexCallId(native_call_id),
            )
            if not runtime_dependencies.translator_indexes.has_shell(self._finished_shells, source_key, shell_id):
                candidates.append(shell_id)
        return candidates

    def _process_shell(
        self,
        raw_event: dependencies.translator_service_dependencies.raw_events.RawEvent,
        process_id: dependencies.translator_id_dependencies.ids_session_types.CodexShellId,
        *,
        recover: bool = True,
    ) -> dependencies.translator_type_dependencies.ids.ShellId | None:
        """Resolve a Codex process after the application restarts.

        A yielded exec result joins the wrapper call id to the native process
        id. This link is in the rollout, but the fast path also keeps it in
        memory. Search the earlier rollout when new translator memory does not
        have it. A recovered link also proves that the shell was backgrounded,
        so its later completion must close the running-work set.

        Returns:
            The shell id.

        """
        source_key = event_dependencies.translator_started_events.source_key(raw_event)
        try:
            return self._process_shells[source_key, process_id]
        except KeyError:
            if not recover:
                return None
            end_position = runtime_dependencies.translator_tool_paths.source_position(raw_event.source_position)
            if end_position is None:
                return None
            # False: the typed result carried the process id. True: the wrapper
            # printed a process reference, valid only when the call says that it
            # can print `r.session_id`.
            # A batch keeps its result until the original call proves the order.
            result_calls: dict[
                dependencies.translator_id_dependencies.ids_session_types.CodexCallId,
                translator_batch_results.ProcessResult,
            ] = {}
            for line in runtime_dependencies.translator_recovery.backward_lines(source_key, end_position):
                call_record = runtime_dependencies.translator_recovery.process_shell_call_from_line(
                    line, process_id, result_calls,
                )
                if call_record is not None:
                    return self._remember_process_shell(source_key, process_id, call_record)
            return None

    def _remember_process_shell(
        self,
        source_key: str,
        process_id: dependencies.translator_id_dependencies.ids_session_types.CodexShellId,
        call_record: dependencies.record_canonical_namespaces.record_tool_records.ExecRecord,
    ) -> dependencies.translator_type_dependencies.ids.ShellId:
        shell_id = dependencies.translator_id_dependencies.ids_session.shell_id_from_codex_call(call_record.call_id)
        self._call_records[source_key, call_record.call_id] = call_record
        self._process_shells[source_key, process_id] = shell_id
        self._backgrounded_shells.add(runtime_dependencies.translator_identity.SourceShellKey(source_key, shell_id))
        return shell_id

    def _collaboration_call(
        self,
        raw_event: dependencies.translator_service_dependencies.raw_events.RawEvent,
        call_id: dependencies.translator_id_dependencies.ids_session_types.CodexCallId,
    ) -> (
        tuple[
            str,
            dependencies.record_payload_namespaces.record_collaboration_registry.CollaborationArguments,
        ]
        | None
    ):
        """Resolve the preceding call without scanning historical rollout data.

        Returns:
            Result items.

        """
        source_path = os.path.realpath(raw_event.source_name)
        key = (source_path, call_id)
        try:
            return self._collaboration_calls[key]
        except KeyError:
            end_position = runtime_dependencies.translator_tool_paths.source_position(raw_event.source_position)
            if end_position is None:
                return None
            # OSError only: a `pydantic.ValidationError` raised while validating
            # recovered arguments must propagate as `translation_failed`.
            for line in runtime_dependencies.translator_recovery.backward_lines(source_path, end_position):
                call = event_dependencies.translator_started_events.collaboration_call_from_line(line, call_id)
                if call is not None:
                    self._collaboration_calls[key] = call
                    return call
            return None

    def _call_record(
        self,
        raw_event: dependencies.translator_service_dependencies.raw_events.RawEvent,
        call_id: dependencies.translator_id_dependencies.ids_session_types.CodexCallId,
    ) -> (
        dependencies.record_canonical_namespaces.record_tool_records.ExecRecord
        | dependencies.record_canonical_namespaces.record_tool_records.ToolRecord
        | dependencies.record_canonical_namespaces.record_actor_records.ToolBatchRecord
        | dependencies.record_canonical_namespaces.record_interaction_records.AskRecord
        | None
    ):
        """Pair an output with the call that opened it.

        The in-memory answer handles the normal adjacent call/output pair. The
        bounded backwards scan handles a daemon restart between those records,
        when the canonical start is durable but translator memory is fresh.

        Returns:
            The call record.

        """
        source_path = event_dependencies.translator_started_events.source_key(raw_event)
        key = (source_path, call_id)
        try:
            return self._call_records[key]
        except KeyError:
            end_position = runtime_dependencies.translator_tool_paths.source_position(raw_event.source_position) or 0
            # As in _collaboration_call, a validation error from a recovered
            # call must propagate. It must not be treated as a missing call.
            for line in runtime_dependencies.translator_recovery.backward_lines(source_path, end_position):
                opened = event_dependencies.translator_started_events.call_from_line(line, call_id)
                if opened is not None:
                    self._call_records[key] = opened
                    return opened
            self._call_records[key] = None
            return None


class _CodexSourceTranslator(_CodexPendingShells):
    """Translate source records and hook events."""

    def _translate(
        self,
        raw_event: dependencies.translator_service_dependencies.raw_events.RawEvent,
        translation_stage: stages.TranslationStage,
    ) -> dependencies.translator_service_dependencies.raw_events.TranslationResult:
        raw_text = event_dependencies.translator_selection_events.decoded_rollout(raw_event)
        if raw_event.source_type == "hook":
            return self._translate_hook_source(raw_event, raw_text, translation_stage)
        if raw_event.source_type == dependencies.translator_service_dependencies.raw_events.TITLE_SOURCE_TYPE:
            if translation_stage == stages.TranslationStage.LIFECYCLE:
                return stages.empty_result(translation_stage)
            return event_dependencies.translator_started_events.translate_title_source(raw_event)
        if raw_event.source_type in {"child_replay", "sidecar_replay"}:
            return dependencies.translator_service_dependencies.raw_events.TranslationResult(
                (),
                dependencies.translator_domain_values.records.RecordedTranslationDecision.IGNORED_NONSEMANTIC,
                "parent history replayed in child rollout",
            )
        return self._translate_rollout_source(raw_event, raw_text, translation_stage)

    def _translate_hook_source(
        self,
        raw_event: dependencies.translator_service_dependencies.raw_events.RawEvent,
        raw_text: str,
        translation_stage: stages.TranslationStage,
    ) -> dependencies.translator_service_dependencies.raw_events.TranslationResult:
        if raw_event.parent_actor_id is not None:
            return dependencies.translator_service_dependencies.raw_events.TranslationResult(
                (),
                dependencies.translator_domain_values.records.RecordedTranslationDecision.IGNORED_NONSEMANTIC,
                "subagent delivery; its activity arrives through the lead's rollout",
            )
        try:
            hook = dependencies.record_payload_namespaces.record_session_meta.CodexHookPayload.model_validate_json(
                raw_text,
            )
        except dependencies.translator_service_dependencies.ValidationError as error:
            msg = "malformed Codex hook delivery"
            raise dependencies.translator_service_dependencies.raw_events.TranslationError(
                msg,
                context=raw_event.source_position,
            ) from error
        events = self._translate_hook(raw_event, hook, translation_stage)
        if events:
            return dependencies.translator_service_dependencies.raw_events.TranslationResult(
                tuple(events),
                dependencies.translator_domain_values.records.RecordedTranslationDecision.TRANSLATED,
            )
        return dependencies.translator_service_dependencies.raw_events.TranslationResult(
            (),
            dependencies.translator_domain_values.records.RecordedTranslationDecision.IGNORED_NONSEMANTIC,
            "hook carries no unique canonical activity",
        )

    def _translate_rollout_source(
        self,
        raw_event: dependencies.translator_service_dependencies.raw_events.RawEvent,
        raw_text: str,
        translation_stage: stages.TranslationStage,
    ) -> dependencies.translator_service_dependencies.raw_events.TranslationResult:
        try:
            header = dependencies.record_payload_namespaces.record_rollout_headers.RolloutHeader.model_validate_json(
                raw_text,
            )
        except dependencies.translator_service_dependencies.ValidationError as error:
            raise dependencies.translator_service_dependencies.raw_events.TranslationError(
                MALFORMED_ROLLOUT_RECORD,
                context=raw_event.source_position,
            ) from error
        if header.type == "session_meta":
            return self._session_metadata_result(raw_event, raw_text, translation_stage)
        if translation_stage == stages.TranslationStage.LIFECYCLE:
            return stages.empty_result(translation_stage)
        record = dependencies.translator_codex_dependencies.rollout.parse_line(raw_text)
        if record is None:
            return dependencies.translator_service_dependencies.raw_events.TranslationResult(
                (),
                dependencies.translator_domain_values.records.RecordedTranslationDecision.IGNORED_UNKNOWN,
                f"unhandled Codex record {header.type!r}",
            )
        if isinstance(record, dependencies.record_canonical_namespaces.record_terminal_records.BadRecord):
            raise dependencies.translator_service_dependencies.raw_events.TranslationError(
                MALFORMED_ROLLOUT_RECORD,
                context=raw_event.source_position,
            )

        observation = (
            dependencies.record_payload_namespaces.record_rollout_headers.RolloutObservation.model_validate_json(
                raw_text,
            )
        )
        events = typing.cast("RecordTranslator", self)._translate_record(raw_event, observation, record)  # noqa: SLF001 -- The cast still refers to self.
        if not events:
            return dependencies.translator_service_dependencies.raw_events.TranslationResult(
                (),
                dependencies.translator_domain_values.records.RecordedTranslationDecision.IGNORED_NONSEMANTIC,
                f"nonsemantic Codex record {record.kind!r}",
            )
        return dependencies.translator_service_dependencies.raw_events.TranslationResult(
            tuple(events),
            dependencies.translator_domain_values.records.RecordedTranslationDecision.TRANSLATED,
        )

    def _session_metadata_result(
        self,
        raw_event: dependencies.translator_service_dependencies.raw_events.RawEvent,
        raw_text: str,
        translation_stage: stages.TranslationStage,
    ) -> dependencies.translator_service_dependencies.raw_events.TranslationResult:
        if translation_stage == stages.TranslationStage.ACTIVITY and raw_event.parent_actor_id is None:
            return stages.empty_result(translation_stage)
        if raw_event.source_position != "0":
            return dependencies.translator_service_dependencies.raw_events.TranslationResult(
                (),
                dependencies.translator_domain_values.records.RecordedTranslationDecision.IGNORED_NONSEMANTIC,
                "replayed session metadata",
            )
        rollout_headers = dependencies.record_payload_namespaces.record_rollout_headers
        session_metadata = dependencies.record_payload_namespaces.record_session_meta
        metadata = (
            rollout_headers.RolloutDocument[session_metadata.SessionMetaPayload].model_validate_json(raw_text).payload
        )
        if raw_event.parent_actor_id is not None:
            return event_dependencies.translator_child_events.child_actor_started(raw_event, metadata)
        return dependencies.translator_service_dependencies.raw_events.TranslationResult(
            tuple(
                typing.cast("_CodexSessionTranslator", self)._session_started_events(  # noqa: SLF001 -- The cast still refers to self.
                    raw_event,
                    metadata.cwd or "",
                    os.path.realpath(raw_event.source_name),
                    continued_from=self._continued_from(
                        raw_event,
                        None if metadata.forked_from_id is None else str(metadata.forked_from_id),
                    ),
                ),
            ),
            dependencies.translator_domain_values.records.RecordedTranslationDecision.TRANSLATED,
        )

    def _translate_hook(
        self,
        raw_event: dependencies.translator_service_dependencies.raw_events.RawEvent,
        codex_hook_payload: dependencies.record_payload_namespaces.record_session_meta.CodexHookPayload,
        translation_stage: stages.TranslationStage,
    ) -> list[
        dependencies.translator_type_dependencies.event_base.CanonicalEvent[
            dependencies.translator_type_dependencies.event_base.EventPayload
        ]
    ]:
        hook_name = codex_hook_payload.hook_event_name or ""
        native_identity = codex_hook_payload.hook_event_id or codex_hook_payload.uuid or raw_event.source_position
        run_started = (
            [] if translation_stage == stages.TranslationStage.ACTIVITY
            else self._hook_run_started_events(raw_event, codex_hook_payload)
        )
        if hook_name == "SessionStart" or translation_stage == stages.TranslationStage.LIFECYCLE:
            return run_started
        if hook_name == "PreCompact":
            self._compactions[raw_event.session_id, str(raw_event.actor_id)] = (
                codex_hook_payload.before_tokens,
                None,
            )
            return [
                *run_started,
                dependencies.translator_codex_dependencies.support.event(
                    raw_event,
                    dependencies.translator_service_dependencies.CanonicalEventDraft(
                        "compaction",
                        native_identity,
                        runtime_dependencies.translator_core_values.STARTED_PHASE,
                        dependencies.translator_domain_values.event_telemetry.CompactionStarted(
                            codex_hook_payload.before_tokens,
                        ),
                    ),
                ),
            ]
        if hook_name == "PostCompact":
            self._remember_post_compaction(raw_event, codex_hook_payload)
        return run_started

    def _remember_post_compaction(
        self,
        raw_event: dependencies.translator_service_dependencies.raw_events.RawEvent,
        codex_hook_payload: dependencies.record_payload_namespaces.record_session_meta.CodexHookPayload,
    ) -> None:
        key = raw_event.session_id, str(raw_event.actor_id)
        previous = self._compactions.get(key, (None, None))
        self._compactions[key] = (
            previous[0] if codex_hook_payload.before_tokens is None else codex_hook_payload.before_tokens,
            codex_hook_payload.after_tokens,
        )

    def _hook_run_started_events(
        self,
        raw_event: dependencies.translator_service_dependencies.raw_events.RawEvent,
        codex_hook_payload: dependencies.record_payload_namespaces.record_session_meta.CodexHookPayload,
    ) -> list[
        dependencies.translator_type_dependencies.event_base.CanonicalEvent[
            dependencies.translator_type_dependencies.event_base.EventPayload
        ]
    ]:
        """Let any lead hook confirm a run whose SessionStart hook was missed.

        All hooks from one terminal window produce the same canonical start
        identities. Normal deliveries therefore converge on the SessionStart
        facts. A later hook repairs the run only when those facts are absent.

        Returns:
            Result items.

        """
        if raw_event.parent_actor_id is not None:
            return []
        if codex_hook_payload.hook_event_name != "SessionStart" and raw_event.terminal_window_id is None:
            return []
        path = codex_hook_payload.transcript_path or ""
        if not dependencies.translator_codex_dependencies.source_catalog.lead_rollout(path):
            # A subagent thread announces no session of its own.
            return []
        metadata = dependencies.translator_codex_dependencies.source_catalog.session_metadata(path)
        return typing.cast("_CodexSessionTranslator", self)._session_started_events(  # noqa: SLF001 -- The cast still refers to self.
            raw_event,
            codex_hook_payload.cwd or "",
            os.path.realpath(path),
            continued_from=self._continued_from(
                raw_event,
                str(metadata.forked_from_id) if metadata is not None and metadata.forked_from_id is not None else None,
            ),
        )


class _CodexSessionTranslator(_CodexSourceTranslator):
    """Translate session and turn state changes."""

    def _session_started_events(
        self,
        raw_event: dependencies.translator_service_dependencies.raw_events.RawEvent,
        working_directory: str,
        source_reference: str,
        *,
        occurred_at: float | None = None,
        continued_from: dependencies.translator_type_dependencies.ids.SessionId | None = None,
    ) -> list[
        dependencies.translator_type_dependencies.event_base.CanonicalEvent[
            dependencies.translator_type_dependencies.event_base.EventPayload
        ]
    ]:
        self._working_directories[str(raw_event.session_id)] = working_directory
        session_started = dependencies.translator_domain_events.event_session.SessionStarted(
            working_directory=working_directory,
            source_reference=source_reference,
            resumed_from=None,
            title=None,
            model=None,
            effort=None,
            account=None,
            continued_from=continued_from,
        )
        actor_started = dependencies.translator_domain_events.event_actor.ActorStarted(
            "codex",
            dependencies.translator_domain_values.messaging.ActorRole.LEAD,
        )
        if raw_event.source_type == "hook" and raw_event.terminal_window_id is not None:
            return list(
                dependencies.translator_service_dependencies.session_run_started_events(
                    raw_event,
                    session_started,
                    actor_started,
                    occurred_at=occurred_at,
                ),
            )
        return [
            dependencies.translator_codex_dependencies.support.event(
                raw_event,
                dependencies.translator_service_dependencies.CanonicalEventDraft(
                    "session",
                    str(raw_event.session_id),
                    runtime_dependencies.translator_core_values.STARTED_PHASE,
                    session_started,
                    occurred_at=occurred_at,
                ),
            ),
            dependencies.translator_codex_dependencies.support.event(
                raw_event,
                dependencies.translator_service_dependencies.CanonicalEventDraft(
                    "actor",
                    str(raw_event.actor_id),
                    runtime_dependencies.translator_core_values.STARTED_PHASE,
                    actor_started,
                    occurred_at=occurred_at,
                ),
            ),
        ]

    # Record kinds that carry a `call_id`/`turn`/`at` field of the same NAME —
    # narrowed here once so the branches below read `record.call_id` etc.
    # directly rather than re-deriving the tuple per branch.
    _call_id_record_types = (
        dependencies.record_canonical_namespaces.record_tool_records.ExecRecord,
        dependencies.record_canonical_namespaces.record_tool_records.ExecResultRecord,
        dependencies.record_canonical_namespaces.record_tool_records.StdinRecord,
        dependencies.record_canonical_namespaces.record_tool_records.ToolRecord,
        dependencies.record_canonical_namespaces.record_interaction_records.PatchCallRecord,
        dependencies.record_canonical_namespaces.record_interaction_records.AskRecord,
        dependencies.record_canonical_namespaces.record_actor_records.ActorActivityRecord,
        dependencies.record_canonical_namespaces.record_actor_records.CollaborationCallRecord,
        dependencies.record_canonical_namespaces.record_actor_records.TaskListRecord,
        dependencies.record_canonical_namespaces.record_actor_records.GoalToolRecord,
        dependencies.record_canonical_namespaces.record_actor_records.ToolBatchRecord,
    )
    _timestamped_record_types = (
        dependencies.record_canonical_namespaces.record_task_records.TaskStartedRecord,
        dependencies.record_canonical_namespaces.record_task_records.TaskCompleteRecord,
    )

    def _record_source(
        self,
        raw_event: dependencies.translator_service_dependencies.raw_events.RawEvent,
        observation: dependencies.record_payload_namespaces.record_rollout_headers.RolloutObservation,
        record: dependencies.record_canonical_namespaces.record_terminal_records.RolloutRecord,
    ) -> runtime_dependencies.translator_state_models.RecordSource:
        record_call_id = record.call_id if isinstance(record, self._call_id_record_types) else None
        native_payload_id = None if observation.payload is None else observation.payload.id
        native_item_id = None if observation.payload is None else observation.payload.item_id
        native_identity = str(
            record_call_id or native_payload_id or native_item_id or raw_event.source_position,
        )
        occurred_at = dependencies.translator_codex_dependencies.support.timestamp(observation.timestamp)
        if occurred_at is None:
            occurred_at = dependencies.translator_codex_dependencies.support.timestamp(
                record.at if isinstance(record, self._timestamped_record_types) else None,
            )
        return runtime_dependencies.translator_state_models.RecordSource(
            raw_event,
            event_dependencies.translator_started_events.source_key(
                raw_event,
            ),
            observation.payload,
            native_identity,
            occurred_at,
        )

    def _task_started(
        self,
        source: runtime_dependencies.translator_state_models.RecordSource,
        record: dependencies.record_canonical_namespaces.record_task_records.TaskStartedRecord,
    ) -> list[
        dependencies.translator_type_dependencies.event_base.CanonicalEvent[
            dependencies.translator_type_dependencies.event_base.EventPayload
        ]
    ]:
        source_key = source.source_key
        session_id = source.raw_event.session_id
        native_turn_id = dependencies.translator_id_dependencies.ids_conversation_types.CodexTurnId(
            record.turn or f"{session_id}:{source.native_identity}",
        )
        self._active_turns[source_key] = native_turn_id
        turn_id = dependencies.translator_id_dependencies.ids_conversation.turn_id_from_codex(native_turn_id)
        events = [
            dependencies.translator_codex_dependencies.support.event(
                source.raw_event,
                dependencies.translator_service_dependencies.CanonicalEventDraft(
                    "turn",
                    str(turn_id),
                    runtime_dependencies.translator_core_values.STARTED_PHASE,
                    dependencies.translator_domain_events.event_conversation.TurnStarted(None),
                    turn_id=turn_id,
                    occurred_at=source.occurred_at,
                ),
            ),
        ]
        if source.raw_event.parent_actor_id is not None and source.raw_event.source_type == "child_rollout":
            events.append(
                event_dependencies.translator_child_events.child_assignment_started(source, native_turn_id, turn_id),
            )
        return events

    def _task_completed(
        self,
        source: runtime_dependencies.translator_state_models.RecordSource,
        record: dependencies.record_canonical_namespaces.record_task_records.TaskCompleteRecord,
    ) -> list[
        dependencies.translator_type_dependencies.event_base.CanonicalEvent[
            dependencies.translator_type_dependencies.event_base.EventPayload
        ]
    ]:
        source_key = source.source_key
        session_id = source.raw_event.session_id
        native_turn_id = dependencies.translator_id_dependencies.ids_conversation_types.CodexTurnId(
            record.turn or self._active_turns.get(source_key) or f"{session_id}:{source.native_identity}",
        )
        if self._active_turns.get(source_key) == native_turn_id:
            self._active_turns.pop(source_key, None)
        turn_id = dependencies.translator_id_dependencies.ids_conversation.turn_id_from_codex(native_turn_id)
        events = [
            dependencies.translator_codex_dependencies.support.event(
                source.raw_event,
                dependencies.translator_service_dependencies.CanonicalEventDraft(
                    "turn",
                    str(turn_id),
                    runtime_dependencies.translator_core_values.FINISHED_PHASE,
                    dependencies.translator_domain_events.event_conversation.TurnFinished(
                        None,
                        dependencies.translator_domain_values.outcomes.Outcome.SUCCEEDED,
                    ),
                    turn_id=turn_id,
                    occurred_at=source.occurred_at,
                ),
            ),
        ]
        if source.raw_event.parent_actor_id is not None and source.raw_event.source_type == "child_rollout":
            events.append(
                event_dependencies.translator_child_events.child_assignment_finished(
                    source, record, native_turn_id, turn_id,
                ),
            )
        return events

    def _turn_aborted(
        self,
        source: runtime_dependencies.translator_state_models.RecordSource,
        record: dependencies.record_canonical_namespaces.record_task_records.TurnAbortedRecord,
    ) -> list[
        dependencies.translator_type_dependencies.event_base.CanonicalEvent[
            dependencies.translator_type_dependencies.event_base.EventPayload
        ]
    ]:
        source_key = source.source_key
        payload_turn_id = ""
        if source.native_payload is not None:
            payload_turn_id = str(source.native_payload.turn_id or "")
        native_turn_id = dependencies.translator_id_dependencies.ids_conversation_types.CodexTurnId(
            record.turn or payload_turn_id or self._active_turns.get(source_key) or source.native_identity,
        )
        if self._active_turns.get(source_key) == native_turn_id:
            self._active_turns.pop(source_key, None)
        turn_id = dependencies.translator_id_dependencies.ids_conversation.turn_id_from_codex(native_turn_id)
        events = [
            dependencies.translator_codex_dependencies.support.event(
                source.raw_event,
                dependencies.translator_service_dependencies.CanonicalEventDraft(
                    "turn",
                    str(turn_id),
                    "aborted",
                    dependencies.translator_domain_events.event_conversation.TurnAborted(None),
                    turn_id=turn_id,
                    occurred_at=source.occurred_at,
                ),
            ),
            *self._cancelled_shell_events(source, source_key, native_turn_id, turn_id),
            *typing.cast("_CodexTurnTranslator", self)._cancelled_skill_events(  # noqa: SLF001 -- The cast still refers to self.
                source,
                source_key,
                native_turn_id,
                turn_id,
            ),
        ]
        if source.raw_event.parent_actor_id is not None and source.raw_event.source_type == "child_rollout":
            events.append(
                event_dependencies.translator_conversation_events.cancelled_child_assignment(
                    source, native_turn_id, turn_id,
                ),
            )
        return events

    def _cancelled_shell_events(
        self,
        source: runtime_dependencies.translator_state_models.RecordSource,
        source_key: str,
        native_turn_id: dependencies.translator_id_dependencies.ids_conversation_types.CodexTurnId,
        turn_id: dependencies.translator_type_dependencies.ids.TurnId,
    ) -> list[
        dependencies.translator_type_dependencies.event_base.CanonicalEvent[
            dependencies.translator_type_dependencies.event_base.EventPayload
        ]
    ]:
        events: list[
            dependencies.translator_type_dependencies.event_base.CanonicalEvent[
                dependencies.translator_type_dependencies.event_base.EventPayload
            ]
        ] = []
        for shell_id in self._interrupted_shells(source_key, native_turn_id):
            self._finished_shells.add(runtime_dependencies.translator_identity.SourceShellKey(source_key, shell_id))
            self._finished_shell_outcomes.add(
                runtime_dependencies.translator_identity.FinishedShellKey(
                    source_key, shell_id, dependencies.translator_domain_values.outcomes.Outcome.CANCELLED,
                ),
            )
            events.append(event_dependencies.translator_child_events.cancelled_shell_event(source, shell_id, turn_id))
            if runtime_dependencies.translator_indexes.has_shell(self._backgrounded_shells, source_key, shell_id):
                self._backgrounded_shells.discard(
                    runtime_dependencies.translator_identity.SourceShellKey(source_key, shell_id),
                )
                events.append(
                    event_dependencies.translator_child_events.cancelled_output_event(source, shell_id, turn_id),
                )
        return events

    def _interrupted_shells(
        self,
        source_key: str,
        native_turn_id: dependencies.translator_id_dependencies.ids_conversation_types.CodexTurnId,
    ) -> list[dependencies.translator_type_dependencies.ids.ShellId]:
        interrupted_shells = []
        for exec_call in typing.cast("_CodexTurnTranslator", self)._turn_exec_calls(source_key, native_turn_id):  # noqa: SLF001 -- The cast still refers to self.
            if runtime_dependencies.translator_tool_paths.read_skill_name(exec_call.record.cmd) is not None:
                continue
            shell_id = dependencies.translator_id_dependencies.ids_session.shell_id_from_codex_call(
                dependencies.translator_id_dependencies.ids_session_types.CodexCallId(exec_call.native_call_id),
            )
            if not runtime_dependencies.translator_indexes.has_shell(self._finished_shells, source_key, shell_id):
                interrupted_shells.append(shell_id)
        return interrupted_shells


class _CodexTurnTranslator(_CodexSessionTranslator):
    """Translate interrupted turn state."""

    def _cancelled_skill_events(
        self,
        source: runtime_dependencies.translator_state_models.RecordSource,
        source_key: str,
        native_turn_id: dependencies.translator_id_dependencies.ids_conversation_types.CodexTurnId,
        turn_id: dependencies.translator_type_dependencies.ids.TurnId,
    ) -> list[
        dependencies.translator_type_dependencies.event_base.CanonicalEvent[
            dependencies.translator_type_dependencies.event_base.EventPayload
        ]
    ]:
        events: list[
            dependencies.translator_type_dependencies.event_base.CanonicalEvent[
                dependencies.translator_type_dependencies.event_base.EventPayload
            ]
        ] = []
        for skill_id in self._interrupted_skills(source_key, native_turn_id):
            self._finished_skills.add(runtime_dependencies.translator_identity.SourceSkillKey(source_key, skill_id))
            events.append(event_dependencies.translator_child_events.cancelled_skill_event(source, skill_id, turn_id))
        return events

    def _interrupted_skills(
        self,
        source_key: str,
        native_turn_id: dependencies.translator_id_dependencies.ids_conversation_types.CodexTurnId,
    ) -> list[dependencies.translator_type_dependencies.ids.SkillId]:
        interrupted_skills = []
        for exec_call in self._turn_exec_calls(source_key, native_turn_id):
            if runtime_dependencies.translator_tool_paths.read_skill_name(exec_call.record.cmd) is None:
                continue
            skill_id = dependencies.translator_id_dependencies.ids_work.skill_id_from_codex(
                dependencies.translator_id_dependencies.ids_work_types.CodexSkillId(exec_call.native_call_id),
            )
            if (
                runtime_dependencies.translator_identity.SourceSkillKey(source_key, skill_id)
                not in self._finished_skills
            ):
                interrupted_skills.append(skill_id)
        return interrupted_skills

    def _turn_exec_calls(
        self,
        source_key: str,
        native_turn_id: dependencies.translator_id_dependencies.ids_conversation_types.CodexTurnId,
    ) -> dependencies.translator_type_dependencies.Iterator[runtime_dependencies.translator_identity.TurnExecCall]:
        """Read shell calls that belong to one source turn.

        Yields:
            The native call identity and record for each matching shell call.

        """
        for (known_source, native_call_id), call_record in self._call_records.items():
            if known_source != source_key:
                continue
            if not isinstance(call_record, dependencies.record_canonical_namespaces.record_tool_records.ExecRecord):
                continue
            if call_record.turn == native_turn_id:
                yield runtime_dependencies.translator_identity.TurnExecCall(
                    dependencies.translator_id_dependencies.ids_session_types.CodexCallId(native_call_id),
                    call_record,
                )

    def _actor_activity(
        self,
        source: runtime_dependencies.translator_state_models.RecordSource,
        record: dependencies.record_canonical_namespaces.record_actor_records.ActorActivityRecord,
    ) -> list[
        dependencies.translator_type_dependencies.event_base.CanonicalEvent[
            dependencies.translator_type_dependencies.event_base.EventPayload
        ]
    ]:
        if record.activity == runtime_dependencies.translator_core_values.COMPLETED_STATUS:
            return []
        call_id, call_name, call_arguments = self._validated_activity_call(source, record)
        if record.activity == "interacted":
            if call_name == "followup_task":
                return []
            if call_name != "send_message" or not isinstance(
                call_arguments,
                dependencies.record_payload_namespaces.record_collaboration_arguments.SendMessageArguments,
            ):
                msg = f"Codex actor interaction came from {call_name!r}"
                raise dependencies.translator_service_dependencies.raw_events.TranslationError(
                    msg,
                )
            return [
                event_dependencies.translator_actor_events.actor_message_event(
                    source, record, call_id, call_arguments,
                ),
            ]
        if record.activity in {runtime_dependencies.translator_core_values.STARTED_PHASE, "interrupted"}:
            return []
        msg = f"unknown Codex actor activity: {record.activity!r}"
        raise dependencies.translator_service_dependencies.raw_events.TranslationError(
            msg,
        )

    def _validated_activity_call(
        self,
        source: runtime_dependencies.translator_state_models.RecordSource,
        record: dependencies.record_canonical_namespaces.record_actor_records.ActorActivityRecord,
    ) -> tuple[
        dependencies.translator_id_dependencies.ids_session_types.CodexCallId,
        str,
        dependencies.record_payload_namespaces.record_collaboration_registry.CollaborationArguments,
    ]:
        call_id = dependencies.translator_id_dependencies.ids_session_types.CodexCallId(record.call_id or "")
        call_name, call_arguments = self._required_collaboration_call(source, call_id)
        expected_call = runtime_dependencies.translator_core_values.ACTIVITY_CALLS.get(record.activity)
        if expected_call is not None and call_name != expected_call:
            msg = f"Codex actor activity {record.activity!r} came from {call_name!r}"
            raise dependencies.translator_service_dependencies.raw_events.TranslationError(
                msg,
            )
        return call_id, call_name, call_arguments

    def _required_collaboration_call(
        self,
        source: runtime_dependencies.translator_state_models.RecordSource,
        call_id: dependencies.translator_id_dependencies.ids_session_types.CodexCallId,
    ) -> tuple[
        str,
        dependencies.record_payload_namespaces.record_collaboration_registry.CollaborationArguments,
    ]:
        call = self._collaboration_call(source.raw_event, call_id)
        if call is None:
            reported_call_id = call_id or runtime_dependencies.translator_core_values.MISSING_NATIVE_VALUE
            msg = f"Codex actor activity has no collaboration call: {reported_call_id}"
            raise dependencies.translator_service_dependencies.raw_events.TranslationError(
                msg,
            )
        return call

    def _goal_changed(
        self,
        source: runtime_dependencies.translator_state_models.RecordSource,
        record: dependencies.record_canonical_namespaces.record_actor_records.GoalRecord,
    ) -> dependencies.translator_type_dependencies.event_base.CanonicalEvent[
        dependencies.translator_type_dependencies.event_base.EventPayload
    ]:
        state = event_dependencies.translator_selection_events.goal_state(record.status or "")
        objective = (record.objective or "").strip() or None
        if state != dependencies.translator_domain_values.work_state.GoalState.CLEARED and objective is None:
            raise dependencies.translator_service_dependencies.raw_events.TranslationError(
                MISSING_GOAL_OBJECTIVE,
            )
        payload = dependencies.translator_domain_values.event_work.GoalChanged(
            objective,
            state,
            event_dependencies.translator_selection_events.goal_reason(record.reason),
        )
        self._goals[str(source.raw_event.session_id)] = payload
        return dependencies.translator_codex_dependencies.support.event(
            source.raw_event,
            dependencies.translator_service_dependencies.CanonicalEventDraft(
                "goal",
                source.native_identity,
                runtime_dependencies.translator_core_values.CHANGED_PHASE,
                payload,
                occurred_at=source.occurred_at,
            ),
        )


class _CodexActivityTranslator(_CodexTurnTranslator):
    """Translate activity and goal changes."""

    def _tool_batch(
        self,
        source: runtime_dependencies.translator_state_models.RecordSource,
        observation: dependencies.record_payload_namespaces.record_rollout_headers.RolloutObservation,
        record: dependencies.record_canonical_namespaces.record_actor_records.ToolBatchRecord,
    ) -> list[
        dependencies.translator_type_dependencies.event_base.CanonicalEvent[
            dependencies.translator_type_dependencies.event_base.EventPayload
        ]
    ]:
        source_key = source.source_key
        batch_call_id = dependencies.translator_id_dependencies.ids_session_types.CodexCallId(
            record.call_id or source.native_identity,
        )
        self._call_records[source_key, batch_call_id] = record
        events: list[
            dependencies.translator_type_dependencies.event_base.CanonicalEvent[
                dependencies.translator_type_dependencies.event_base.EventPayload
            ]
        ] = []
        for action in record.actions:
            events.extend(self._batch_action_events(source, observation, action))
        return events

    def _batch_action_events(
        self,
        source: runtime_dependencies.translator_state_models.RecordSource,
        observation: dependencies.record_payload_namespaces.record_rollout_headers.RolloutObservation,
        action: dependencies.record_canonical_namespaces.record_tool_records.ExecRecord
        | dependencies.record_canonical_namespaces.record_tool_records.StdinRecord
        | dependencies.record_canonical_namespaces.record_tool_records.ToolRecord
        | dependencies.record_canonical_namespaces.record_actor_records.TaskListRecord
        | dependencies.record_canonical_namespaces.record_actor_records.GoalToolRecord
        | dependencies.record_canonical_namespaces.record_actor_records.CollaborationCallRecord,
    ) -> list[
        dependencies.translator_type_dependencies.event_base.CanonicalEvent[
            dependencies.translator_type_dependencies.event_base.EventPayload
        ]
    ]:
        if isinstance(
            action,
            (
                dependencies.record_canonical_namespaces.record_actor_records.CollaborationCallRecord,
                dependencies.record_canonical_namespaces.record_tool_records.ExecRecord,
                dependencies.record_canonical_namespaces.record_tool_records.StdinRecord,
                dependencies.record_canonical_namespaces.record_tool_records.ToolRecord,
                dependencies.record_canonical_namespaces.record_actor_records.TaskListRecord,
            ),
        ):
            return typing.cast("RecordTranslator", self)._translate_record(  # noqa: SLF001 -- The cast still refers to self.
                source.raw_event,
                observation,
                action,
            )
        if action.name == "get_goal":
            return []
        return [self._goal_tool_event(source, action)]

    def _goal_tool_event(
        self,
        source: runtime_dependencies.translator_state_models.RecordSource,
        action: dependencies.record_canonical_namespaces.record_actor_records.GoalToolRecord,
    ) -> dependencies.translator_type_dependencies.event_base.CanonicalEvent[
        dependencies.translator_type_dependencies.event_base.EventPayload
    ]:
        objective, native_state = self._goal_tool_details(source, action)
        state = event_dependencies.translator_selection_events.goal_state(native_state)
        if objective is None:
            raise dependencies.translator_service_dependencies.raw_events.TranslationError(
                MISSING_GOAL_OBJECTIVE,
            )
        goal_changed = dependencies.translator_domain_values.event_work.GoalChanged(
            objective,
            state,
            event_dependencies.translator_selection_events.goal_reason(action.reason),
        )
        self._goals[str(source.raw_event.session_id)] = goal_changed
        return dependencies.translator_codex_dependencies.support.event(
            source.raw_event,
            dependencies.translator_service_dependencies.CanonicalEventDraft(
                "goal",
                str(action.call_id),
                runtime_dependencies.translator_core_values.CHANGED_PHASE,
                goal_changed,
                occurred_at=source.occurred_at,
            ),
        )

    def _goal_tool_details(
        self,
        source: runtime_dependencies.translator_state_models.RecordSource,
        action: dependencies.record_canonical_namespaces.record_actor_records.GoalToolRecord,
    ) -> tuple[str | None, str]:
        if action.name == "create_goal":
            objective = (action.objective or "").strip() or None
            return objective, action.status or "active"
        if action.name == "update_goal":
            previous_goal = self._goals.get(str(source.raw_event.session_id))
            previous_objective = None if previous_goal is None else previous_goal.objective
            objective = (action.objective or "").strip() or previous_objective
            return objective, action.status or ""
        reported_name = action.name or runtime_dependencies.translator_core_values.MISSING_NATIVE_VALUE
        msg = f"unknown Codex goal tool: {reported_name}"
        raise dependencies.translator_service_dependencies.raw_events.TranslationError(
            msg,
        )

    def _task_list_events(
        self,
        source: runtime_dependencies.translator_state_models.RecordSource,
        record: dependencies.record_canonical_namespaces.record_actor_records.TaskListRecord,
    ) -> list[
        dependencies.translator_type_dependencies.event_base.CanonicalEvent[
            dependencies.translator_type_dependencies.event_base.EventPayload
        ]
    ]:
        call_id = dependencies.translator_id_dependencies.ids_session_types.CodexCallId(
            record.call_id or source.native_identity,
        )
        self._semantic_tool_calls.add(
            runtime_dependencies.translator_identity.SourceCallKey(source.source_key, call_id),
        )
        plan_key = (str(source.raw_event.session_id), str(source.raw_event.actor_id))
        previous = self._plan_tasks.get(plan_key)
        current = event_dependencies.translator_actor_events.plan_task_changes(source, record)
        events = [
            event_dependencies.translator_actor_events.task_list_event(source, call_id, current),
            *event_dependencies.translator_actor_events.changed_task_events(source, call_id, current, previous),
        ]
        self._plan_tasks[plan_key] = current
        return events

    def _call_started(
        self,
        source: runtime_dependencies.translator_state_models.RecordSource,
        record: dependencies.record_canonical_namespaces.record_tool_records.ExecRecord
        | dependencies.record_canonical_namespaces.record_tool_records.ToolRecord,
    ) -> list[
        dependencies.translator_type_dependencies.event_base.CanonicalEvent[
            dependencies.translator_type_dependencies.event_base.EventPayload
        ]
    ]:
        call_id = dependencies.translator_id_dependencies.ids_session_types.CodexCallId(
            record.call_id or source.native_identity,
        )
        self._call_records[source.source_key, call_id] = record
        if isinstance(record, dependencies.record_canonical_namespaces.record_tool_records.ToolRecord):
            runtime_dependencies.translator_tool_parsing.codex_tool(record.name, record.args)
            return []
        skill_name = runtime_dependencies.translator_tool_paths.read_skill_name(record.cmd)
        if skill_name is not None:
            return [
                event_dependencies.translator_started_events.started_skill_event(source, record, call_id, skill_name),
            ]
        return [event_dependencies.translator_started_events.started_shell_event(source, record, call_id)]

    def _stdin_events(
        self,
        source: runtime_dependencies.translator_state_models.RecordSource,
        record: dependencies.record_canonical_namespaces.record_tool_records.StdinRecord,
    ) -> list[
        dependencies.translator_type_dependencies.event_base.CanonicalEvent[
            dependencies.translator_type_dependencies.event_base.EventPayload
        ]
    ]:
        if not record.process_id:
            msg = "Codex write_stdin has no process session"
            raise dependencies.translator_service_dependencies.raw_events.TranslationError(
                msg,
            )
        source_key = source.source_key
        shell_id = self._process_shell(source.raw_event, record.process_id)
        if shell_id is None:
            # A deferred code cell can return the process ID through `wait`.
            # The next stdin call is the first record that links this ID to the
            # one open command.
            shell_id = self._only_pending_exec_shell(source_key)
            if shell_id is not None:
                self._process_shells[source_key, record.process_id] = shell_id
        if shell_id is None:
            msg = f"Codex write_stdin references unknown process session: {record.process_id}"
            raise dependencies.translator_service_dependencies.raw_events.TranslationError(
                msg,
            )
        call_id = dependencies.translator_id_dependencies.ids_session_types.CodexCallId(
            record.call_id or source.native_identity,
        )
        self._continuation_shells[source_key, call_id] = shell_id
        if not record.text or runtime_dependencies.translator_indexes.has_shell(
            self._finished_shells, source_key, shell_id,
        ):
            return []
        return [event_dependencies.translator_started_events.stdin_input_event(source, record, call_id, shell_id)]


class _CodexToolCallTranslator(_CodexActivityTranslator):
    """Translate tool calls and their input."""

    def _continued_shell_result(
        self,
        source: runtime_dependencies.translator_state_models.RecordSource,
        source_key: str,
        shell_id: dependencies.translator_type_dependencies.ids.ShellId,
        record: dependencies.record_canonical_namespaces.record_tool_records.ExecResultRecord,
    ) -> list[
        dependencies.translator_type_dependencies.event_base.CanonicalEvent[
            dependencies.translator_type_dependencies.event_base.EventPayload
        ]
    ]:
        if runtime_dependencies.translator_indexes.has_shell(self._finished_shells, source_key, shell_id):
            return []
        if not record.output:
            return []
        ordinal = int(source.raw_event.source_position)
        payload = dependencies.translator_domain_events.event_shell.ShellProgressed(
            shell_id,
            ordinal,
            dependencies.translator_domain_values.outcomes.ProgressStream.OUTPUT,
            dependencies.translator_codex_dependencies.support.content(record.output),
            dependencies.translator_domain_values.outcomes.OutputMode.APPEND,
        )
        return [
            dependencies.translator_codex_dependencies.support.event(
                source.raw_event,
                dependencies.translator_service_dependencies.CanonicalEventDraft(
                    runtime_dependencies.translator_core_values.SHELL_SUBJECT,
                    str(shell_id),
                    f"progress:{ordinal}",
                    payload,
                    occurred_at=source.occurred_at,
                ),
            ),
        ]

    def _tool_batch_result(
        self,
        source: runtime_dependencies.translator_state_models.RecordSource,
        call_record: dependencies.record_canonical_namespaces.record_actor_records.ToolBatchRecord,
        record: dependencies.record_canonical_namespaces.record_tool_records.ExecResultRecord,
    ) -> list[
        dependencies.translator_type_dependencies.event_base.CanonicalEvent[
            dependencies.translator_type_dependencies.event_base.EventPayload
        ]
    ]:
        events: list[
            dependencies.translator_type_dependencies.event_base.CanonicalEvent[
                dependencies.translator_type_dependencies.event_base.EventPayload
            ]
        ] = []
        for nested in translator_batch_results.command_results(call_record, record):
            events.extend(typing.cast("RecordTailTranslator", self)._exec_result_events(source, nested))  # noqa: SLF001 -- The cast still refers to self.
        for action in call_record.actions:
            if not isinstance(action, dependencies.record_canonical_namespaces.record_tool_records.ToolRecord):
                continue
            nested_result = dependencies.record_canonical_namespaces.record_tool_records.ExecResultRecord(
                exit=record.exit,
                output=record.output,
                call_id=action.call_id,
                process_id=record.process_id,
                running=record.running,
                interrupted=record.interrupted,
                ts=record.ts,
            )
            events.extend(
                typing.cast("RecordTailTranslator", self)._tool_result(  # noqa: SLF001 -- The cast still refers to self.
                    source.raw_event,
                    action.call_id,
                    action,
                    nested_result,
                    source.occurred_at,
                ),
            )
        return events

    def _skill_result(
        self,
        source: runtime_dependencies.translator_state_models.RecordSource,
        call_id: dependencies.translator_id_dependencies.ids_session_types.CodexCallId,
        call_record: dependencies.record_canonical_namespaces.record_tool_records.ExecRecord,
        record: dependencies.record_canonical_namespaces.record_tool_records.ExecResultRecord,
    ) -> list[
        dependencies.translator_type_dependencies.event_base.CanonicalEvent[
            dependencies.translator_type_dependencies.event_base.EventPayload
        ]
    ]:
        skill_id = dependencies.translator_id_dependencies.ids_work.skill_id_from_codex(
            dependencies.translator_id_dependencies.ids_work_types.CodexSkillId(call_id),
        )
        source_key = source.source_key
        if runtime_dependencies.translator_identity.SourceSkillKey(source_key, skill_id) in self._finished_skills:
            return []
        self._finished_skills.add(runtime_dependencies.translator_identity.SourceSkillKey(source_key, skill_id))
        payload = dependencies.translator_domain_events.event_resource.SkillFinished(
            skill_id,
            runtime_dependencies.translator_shell_state.exec_outcome(record),
            dependencies.translator_codex_dependencies.support.content(record.output) if record.output else None,
        )
        turn_id = (
            dependencies.translator_id_dependencies.ids_conversation.turn_id_from_codex(
                dependencies.translator_id_dependencies.ids_conversation_types.CodexTurnId(call_record.turn),
            )
            if call_record.turn
            else None
        )
        return [
            dependencies.translator_codex_dependencies.support.event(
                source.raw_event,
                dependencies.translator_service_dependencies.CanonicalEventDraft(
                    "skill",
                    str(skill_id),
                    runtime_dependencies.translator_core_values.FINISHED_PHASE,
                    payload,
                    turn_id=turn_id,
                    occurred_at=source.occurred_at,
                ),
            ),
        ]

    def _shell_result(
        self,
        source: runtime_dependencies.translator_state_models.RecordSource,
        call_id: dependencies.translator_id_dependencies.ids_session_types.CodexCallId,
        call_record: dependencies.record_canonical_namespaces.record_tool_records.ExecRecord,
        record: dependencies.record_canonical_namespaces.record_tool_records.ExecResultRecord,
    ) -> list[
        dependencies.translator_type_dependencies.event_base.CanonicalEvent[
            dependencies.translator_type_dependencies.event_base.EventPayload
        ]
    ]:
        context = runtime_dependencies.translator_shell_state.shell_result_context(source, call_id, call_record)
        if (
            runtime_dependencies.translator_identity.SourceShellKey(context.source_key, context.shell_id)
            in self._finished_shells
        ):
            return self._settled_shell_result(context, call_record, record)
        process = runtime_dependencies.translator_shell_state.shell_process(call_record, record)
        if process.process_id:
            self._process_shells[context.source_key, process.process_id] = context.shell_id
        if record.interrupted:
            return self._interrupted_shell_result(context)
        if runtime_dependencies.translator_shell_state.shell_is_running(call_record, record, process):
            return self._running_shell_result(context, process, record)
        return typing.cast("ShellResultTranslator", self)._finished_shell_result(context, process, record)  # noqa: SLF001 -- The cast still refers to self.

    def _settled_shell_result(
        self,
        context: runtime_dependencies.translator_state_models.ShellResultContext,
        call_record: dependencies.record_canonical_namespaces.record_tool_records.ExecRecord,
        record: dependencies.record_canonical_namespaces.record_tool_records.ExecResultRecord,
    ) -> list[
        dependencies.translator_type_dependencies.event_base.CanonicalEvent[
            dependencies.translator_type_dependencies.event_base.EventPayload
        ]
    ]:
        outcome = next(
            (
                finished.outcome
                for finished in self._finished_shell_outcomes
                if finished.source_key == context.source_key and finished.shell_id == context.shell_id
            ),
            None,
        )
        if outcome is None or not runtime_dependencies.translator_shell_state.is_empty_yield(call_record, record):
            return []
        payload = dependencies.translator_domain_events.event_shell.ShellOutputFinished(context.shell_id, outcome)
        return [
            event_dependencies.translator_shell_events.shell_event(context, "settled_after_native_finish", payload),
        ]

    def _interrupted_shell_result(
        self,
        context: runtime_dependencies.translator_state_models.ShellResultContext,
    ) -> list[
        dependencies.translator_type_dependencies.event_base.CanonicalEvent[
            dependencies.translator_type_dependencies.event_base.EventPayload
        ]
    ]:
        key = runtime_dependencies.translator_identity.SourceShellKey(context.source_key, context.shell_id)
        if key in self._backgrounded_shells:
            return []
        self._finished_shells.add(key)
        self._finished_shell_outcomes.add(
            runtime_dependencies.translator_identity.FinishedShellKey(
                context.source_key,
                context.shell_id,
                dependencies.translator_domain_values.outcomes.Outcome.CANCELLED,
            ),
        )
        payload = dependencies.translator_domain_events.event_shell.ShellFinished(
            context.shell_id,
            dependencies.translator_domain_values.outcomes.Outcome.CANCELLED,
            None,
            None,
        )
        return [
            event_dependencies.translator_shell_events.shell_event(
                context, runtime_dependencies.translator_core_values.FINISHED_PHASE, payload,
            ),
        ]

    def _running_shell_result(
        self,
        context: runtime_dependencies.translator_state_models.ShellResultContext,
        process: runtime_dependencies.translator_state_models.ShellProcess,
        record: dependencies.record_canonical_namespaces.record_tool_records.ExecResultRecord,
    ) -> list[
        dependencies.translator_type_dependencies.event_base.CanonicalEvent[
            dependencies.translator_type_dependencies.event_base.EventPayload
        ]
    ]:
        events: list[
            dependencies.translator_type_dependencies.event_base.CanonicalEvent[
                dependencies.translator_type_dependencies.event_base.EventPayload
            ]
        ] = []
        key = runtime_dependencies.translator_identity.SourceShellKey(context.source_key, context.shell_id)
        if key not in self._backgrounded_shells:
            self._backgrounded_shells.add(key)
            events.append(
                event_dependencies.translator_shell_events.shell_event(
                    context,
                    "backgrounded",
                    dependencies.translator_domain_events.event_shell.ShellBackgrounded(context.shell_id),
                ),
            )
        output = "" if process.yielded_with_identity else record.output
        if output:
            events.append(event_dependencies.translator_shell_events.shell_progress_event(context, output))
        return events
