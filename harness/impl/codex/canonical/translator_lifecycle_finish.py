# Copyright (c) 2026 Zhambyl Yermagambet
"""Split Codex canonical translation."""

from __future__ import annotations

import os
import typing

from harness.impl.codex.canonical import (
    translator_dependencies as dependencies,
    translator_lifecycle_finish_dependencies as finish_dependencies,
)
from harness.impl.codex.canonical.translator_lifecycle_start import _CodexToolCallTranslator


class _CodexToolResultTranslator(_CodexToolCallTranslator):
    """Translate shell and tool results."""

    def _finished_shell_result(
        self,
        context: finish_dependencies.translator_state_models.ShellResultContext,
        process: finish_dependencies.translator_state_models.ShellProcess,
        record: dependencies.record_canonical_namespaces.record_tool_records.ExecResultRecord,
    ) -> list[
        dependencies.translator_type_dependencies.event_base.CanonicalEvent[
            dependencies.translator_type_dependencies.event_base.EventPayload
        ]
    ]:
        outcome = dependencies.translator_domain_values.outcomes.Outcome.SUCCEEDED
        if process.exit_code not in {None, 0}:
            outcome = dependencies.translator_domain_values.outcomes.Outcome.FAILED
        payload = dependencies.translator_domain_events.event_shell.ShellFinished(
            context.shell_id,
            outcome,
            dependencies.translator_codex_dependencies.support.content(record.output),
            process.exit_code,
        )
        key = finish_dependencies.translator_identity.SourceShellKey(context.source_key, context.shell_id)
        self._finished_shells.add(key)
        self._finished_shell_outcomes.add(
            finish_dependencies.translator_identity.FinishedShellKey(context.source_key, context.shell_id, outcome),
        )
        events = [
            finish_dependencies.translator_shell_events.shell_event(
                context, finish_dependencies.translator_core_values.FINISHED_PHASE, payload,
            ),
        ]
        if key in self._backgrounded_shells:
            self._backgrounded_shells.discard(key)
            events.append(
                finish_dependencies.translator_shell_events.shell_event(
                    context,
                    "output_finished",
                    dependencies.translator_domain_events.event_shell.ShellOutputFinished(context.shell_id, outcome),
                ),
            )
        return events

    def _remember_collaboration_call(
        self,
        source: finish_dependencies.translator_state_models.RecordSource,
        record: dependencies.record_canonical_namespaces.record_actor_records.CollaborationCallRecord,
    ) -> None:
        source_key = os.path.realpath(source.raw_event.source_name)
        call_id = dependencies.translator_id_dependencies.ids_session_types.CodexCallId(record.call_id or "")
        self._collaboration_calls[source_key, call_id] = record.name, record.args

    def _remember_goal_tool_call(
        self,
        source: finish_dependencies.translator_state_models.RecordSource,
        record: dependencies.record_canonical_namespaces.record_actor_records.GoalToolRecord,
    ) -> None:
        call_id = dependencies.translator_id_dependencies.ids_session_types.CodexCallId(
            record.call_id or source.native_identity,
        )
        self._semantic_tool_calls.add(finish_dependencies.translator_identity.SourceCallKey(source.source_key, call_id))

    def _exec_result_events(
        self,
        record_source: finish_dependencies.translator_state_models.RecordSource,
        record: dependencies.record_canonical_namespaces.record_tool_records.ExecResultRecord,
    ) -> list[
        dependencies.translator_type_dependencies.event_base.CanonicalEvent[
            dependencies.translator_type_dependencies.event_base.EventPayload
        ]
    ]:
        call_id = dependencies.translator_id_dependencies.ids_session_types.CodexCallId(
            record.call_id or record_source.native_identity,
        )
        source_key = record_source.source_key
        if finish_dependencies.translator_identity.SourceCallKey(source_key, call_id) in self._semantic_tool_calls:
            return []
        continued_shell = self._continuation_shells.get((source_key, call_id))
        if continued_shell is not None:
            return self._continued_command_result(record_source, continued_shell, record)
        if (source_key, call_id) in self._collaboration_calls:
            return []
        call_record = self._call_record(record_source.raw_event, call_id)
        if call_record is None:
            return []
        return typing.cast("_CodexRecordTailTranslator", self)._known_call_result(  # noqa: SLF001 -- The cast still refers to self.
            record_source, call_id, call_record, record,
        )

    def _continued_command_result(
        self,
        source: finish_dependencies.translator_state_models.RecordSource,
        shell_id: dependencies.translator_type_dependencies.ids.ShellId,
        record: dependencies.record_canonical_namespaces.record_tool_records.ExecResultRecord,
    ) -> list[
        dependencies.translator_type_dependencies.event_base.CanonicalEvent[
            dependencies.translator_type_dependencies.event_base.EventPayload
        ]
    ]:
        if record.exit is None:
            return self._continued_shell_result(source, source.source_key, shell_id, record)
        if finish_dependencies.translator_identity.SourceShellKey(source.source_key, shell_id) in self._finished_shells:
            return []
        context = finish_dependencies.translator_state_models.ShellResultContext(
            source, source.source_key, shell_id, None,
        )
        process = finish_dependencies.translator_state_models.ShellProcess(
            dependencies.translator_codex_dependencies.support.exit_code(record.exit),
            dependencies.translator_id_dependencies.ids_session_types.CodexShellId(""),
            yielded_with_identity=False,
        )
        return self._finished_shell_result(context, process, record)

    def _command_completed(
        self,
        source: finish_dependencies.translator_state_models.RecordSource,
        record: dependencies.record_canonical_namespaces.record_tool_records.CommandCompletedRecord,
    ) -> list[
        dependencies.translator_type_dependencies.event_base.CanonicalEvent[
            dependencies.translator_type_dependencies.event_base.EventPayload
        ]
    ]:
        context = finish_dependencies.translator_shell_events.command_completion(source, record)
        resolved = self._resolve_completed_shell(context)
        if resolved is None:
            return []
        shell_key = finish_dependencies.translator_identity.SourceShellKey(context.source_key, resolved.shell_id)
        if shell_key in self._finished_shells:
            return []
        return typing.cast("_CodexShellResultTranslator", self)._finish_completed_shell(context, resolved)  # noqa: SLF001 -- The cast still refers to self.

    def _resolve_completed_shell(
        self,
        context: finish_dependencies.translator_state_models.CommandCompletion,
    ) -> finish_dependencies.translator_state_models.ResolvedCompletedShell | None:
        shell_id = self._process_shell(context.source.raw_event, context.record.process_id, recover=False)
        if shell_id is None:
            shell_id = self._pending_exec_shell_for_command(context.source.raw_event, context.record.command)
        if shell_id is None:
            shell_id = self._process_shell(context.source.raw_event, context.record.process_id)
        if shell_id is None:
            shell_id = self._only_pending_exec_shell(context.source_key)
        if shell_id is None:
            return typing.cast("_CodexShellResultTranslator", self)._synthetic_completed_shell(context)  # noqa: SLF001 -- The cast still refers to self.
        self._process_shells[context.source_key, context.record.process_id] = shell_id
        return finish_dependencies.translator_state_models.ResolvedCompletedShell(shell_id, None)


class _CodexShellResultTranslator(_CodexToolResultTranslator):
    """Settle shell results and tool state."""

    def _synthetic_completed_shell(
        self,
        context: finish_dependencies.translator_state_models.CommandCompletion,
    ) -> finish_dependencies.translator_state_models.ResolvedCompletedShell | None:
        if not context.record.command:
            return None
        shell_id = dependencies.translator_id_dependencies.ids_session.shell_id_from_codex_call(
            dependencies.translator_id_dependencies.ids_session_types.CodexCallId(
                context.record.item_id or context.source.native_identity,
            ),
        )
        self._process_shells[context.source_key, context.record.process_id] = shell_id
        command = finish_dependencies.translator_shell_events.completed_command(context.record.command)
        payload = dependencies.translator_domain_events.event_shell.ShellStarted(
            shell_id,
            dependencies.translator_codex_dependencies.support.content(command),
            dependencies.translator_domain_values.outcomes.ExecutionMode.FOREGROUND,
            None,
        )
        started_event = finish_dependencies.translator_shell_events.completed_shell_event(
            context,
            shell_id,
            finish_dependencies.translator_core_values.STARTED_PHASE,
            payload,
        )
        return finish_dependencies.translator_state_models.ResolvedCompletedShell(shell_id, started_event)

    def _finish_completed_shell(
        self,
        context: finish_dependencies.translator_state_models.CommandCompletion,
        resolved: finish_dependencies.translator_state_models.ResolvedCompletedShell,
    ) -> list[
        dependencies.translator_type_dependencies.event_base.CanonicalEvent[
            dependencies.translator_type_dependencies.event_base.EventPayload
        ]
    ]:
        process_exit_code = dependencies.translator_codex_dependencies.support.exit_code(context.record.exit)
        outcome = (
            dependencies.translator_domain_values.outcomes.Outcome.SUCCEEDED
            if process_exit_code == 0
            else dependencies.translator_domain_values.outcomes.Outcome.FAILED
        )
        key = finish_dependencies.translator_identity.SourceShellKey(context.source_key, resolved.shell_id)
        self._finished_shells.add(key)
        self._finished_shell_outcomes.add(
            finish_dependencies.translator_identity.FinishedShellKey(context.source_key, resolved.shell_id, outcome),
        )
        events = finish_dependencies.translator_shell_events.completed_shell_events(
            context, resolved, process_exit_code, outcome,
        )
        # Native completion closes the output even if a restart cleared the
        # background set. Command matching can recover the shell without it.
        self._backgrounded_shells.discard(key)
        events.append(
            finish_dependencies.translator_shell_events.completed_shell_event(
                context,
                resolved.shell_id,
                "output_finished",
                dependencies.translator_domain_events.event_shell.ShellOutputFinished(resolved.shell_id, outcome),
            ),
        )
        return events

    def _mcp_tool_completed(
        self,
        source: finish_dependencies.translator_state_models.RecordSource,
        record: dependencies.record_canonical_namespaces.record_tool_records.McpToolCompletedRecord,
    ) -> list[
        dependencies.translator_type_dependencies.event_base.CanonicalEvent[
            dependencies.translator_type_dependencies.event_base.EventPayload
        ]
    ]:
        native_name = f"mcp__{record.server}__{record.tool}"
        if record.browser_use:
            return [self._browser_completed(source, record, native_name)]
        self._remember_mcp_outcome(source, record, native_name)
        return []

    def _browser_completed(
        self,
        source: finish_dependencies.translator_state_models.RecordSource,
        record: dependencies.record_canonical_namespaces.record_tool_records.McpToolCompletedRecord,
        native_name: str,
    ) -> dependencies.translator_type_dependencies.event_base.CanonicalEvent[
        dependencies.translator_type_dependencies.event_base.EventPayload
    ]:
        source_key = source.source_key
        candidates = self._pending_tool_calls(source_key, {native_name}, include_mcp_outcomes=True)
        if candidates:
            self._finished_tool_calls.add(
                finish_dependencies.translator_identity.SourceCallKey(source_key, candidates[0]),
            )
        action = (record.title or "").strip()
        if not action:
            msg = "Codex browser interaction has no title"
            raise dependencies.translator_service_dependencies.raw_events.TranslationError(
                msg,
            )
        payload = dependencies.translator_domain_events.event_resource.BrowserInteracted(
            action,
            dependencies.translator_codex_dependencies.support.content(record.result) if record.result else None,
            finish_dependencies.translator_shell_events.browser_outcome(record),
        )
        return dependencies.translator_codex_dependencies.support.event(
            source.raw_event,
            dependencies.translator_service_dependencies.CanonicalEventDraft(
                "browser",
                record.item_id or source.native_identity,
                "interacted",
                payload,
                occurred_at=source.occurred_at,
            ),
        )

    def _remember_mcp_outcome(
        self,
        source: finish_dependencies.translator_state_models.RecordSource,
        record: dependencies.record_canonical_namespaces.record_tool_records.McpToolCompletedRecord,
        native_name: str,
    ) -> None:
        source_key = source.source_key
        candidates = self._pending_tool_calls(source_key, {native_name, record.tool}, include_mcp_outcomes=False)
        if not candidates:
            # Dynamic JavaScript tool lookup can have no recorded call.
            # Keep the native record without assigning another call's outcome.
            return
        outcome = finish_dependencies.translator_general_events.mcp_outcome(record.status)
        self._mcp_tool_outcomes[source_key, candidates[0]] = outcome

    def _pending_tool_calls(
        self,
        source_key: str,
        names: set[str],
        *,
        include_mcp_outcomes: bool,
    ) -> list[dependencies.translator_id_dependencies.ids_session_types.CodexCallId]:
        pending_calls = []
        for (known_source, native_call_id), call_record in self._call_records.items():
            if not isinstance(call_record, dependencies.record_canonical_namespaces.record_tool_records.ToolRecord):
                continue
            if self._is_pending_tool_call(
                (known_source, native_call_id),
                call_record,
                source_key,
                names,
                include_mcp_outcomes=include_mcp_outcomes,
            ):
                pending_calls.append(
                    dependencies.translator_id_dependencies.ids_session_types.CodexCallId(native_call_id),
                )
        return pending_calls

    def _is_pending_tool_call(
        self,
        call_key: tuple[str, str],
        call_record: dependencies.record_canonical_namespaces.record_tool_records.ToolRecord,
        source_key: str,
        names: set[str],
        *,
        include_mcp_outcomes: bool,
    ) -> bool:
        """Return whether a stored tool call is pending for a lookup.

        Returns:
            Whether a stored tool call is pending for a lookup.

        """
        if call_key[0] != source_key:
            return False
        finished_call = finish_dependencies.translator_identity.SourceCallKey(
            call_key[0],
            dependencies.translator_id_dependencies.ids_session_types.CodexCallId(call_key[1]),
        )
        if call_record.name not in names or finished_call in self._finished_tool_calls:
            return False
        return include_mcp_outcomes or call_key not in self._mcp_tool_outcomes


class _CodexCompletionTranslator(_CodexShellResultTranslator):
    """Complete shell and browser tool calls."""

    def _selection_events(
        self,
        source: finish_dependencies.translator_state_models.RecordSource,
        record: dependencies.record_canonical_namespaces.record_context_records.TurnContextRecord
        | dependencies.record_canonical_namespaces.record_interaction_records.SettingsRecord,
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
        model_event = self._model_selection_event(source, record)
        if model_event is not None:
            events.append(model_event)
        effort_event = self._effort_selection_event(source, record)
        if effort_event is not None:
            events.append(effort_event)
        return events

    def _model_selection_event(
        self,
        source: finish_dependencies.translator_state_models.RecordSource,
        record: dependencies.record_canonical_namespaces.record_context_records.TurnContextRecord
        | dependencies.record_canonical_namespaces.record_interaction_records.SettingsRecord,
    ) -> (
        dependencies.translator_type_dependencies.event_base.CanonicalEvent[
            dependencies.translator_type_dependencies.event_base.EventPayload
        ]
        | None
    ):
        if not record.model:
            return None
        changed = self._selections.model(
            source.raw_event.session_id,
            source.raw_event.actor_id,
            dependencies.translator_codex_dependencies.support.model_reference(
                dependencies.translator_codex_dependencies.CodexModel(record.model),
            ),
            dependencies.translator_domain_values.work_state.ModelChangeReason.REPORTED_BY_HARNESS,
            record.model,
        )
        return (
            None
            if changed is None
            else finish_dependencies.translator_selection_events.selection_event(source, "model", changed)
        )

    def _effort_selection_event(
        self,
        source: finish_dependencies.translator_state_models.RecordSource,
        record: dependencies.record_canonical_namespaces.record_context_records.TurnContextRecord
        | dependencies.record_canonical_namespaces.record_interaction_records.SettingsRecord,
    ) -> (
        dependencies.translator_type_dependencies.event_base.CanonicalEvent[
            dependencies.translator_type_dependencies.event_base.EventPayload
        ]
        | None
    ):
        if not record.effort:
            return None
        changed = self._selections.effort(
            source.raw_event.session_id,
            source.raw_event.actor_id,
            record.effort,
            dependencies.translator_domain_values.work_state.EffortChangeReason.REPORTED_BY_HARNESS,
        )
        return (
            None
            if changed is None
            else finish_dependencies.translator_selection_events.selection_event(source, "effort", changed)
        )

    def _compaction_finished(
        self,
        source: finish_dependencies.translator_state_models.RecordSource,
        record: dependencies.record_canonical_namespaces.record_interaction_records.CompactBoundaryRecord,
    ) -> dependencies.translator_type_dependencies.event_base.CanonicalEvent[
        dependencies.translator_type_dependencies.event_base.EventPayload
    ]:
        key = source.raw_event.session_id, str(source.raw_event.actor_id)
        before_tokens, after_tokens = self._compactions.pop(key, (None, None))
        payload = dependencies.translator_domain_values.event_telemetry.CompactionFinished(
            before_tokens,
            after_tokens,
            dependencies.translator_codex_dependencies.support.content(record.context, markdown=True)
            if record.context
            else None,
        )
        return dependencies.translator_codex_dependencies.support.event(
            source.raw_event,
            dependencies.translator_service_dependencies.CanonicalEventDraft(
                "compaction",
                str(record.window_id or source.native_identity),
                finish_dependencies.translator_core_values.FINISHED_PHASE,
                payload,
                occurred_at=source.occurred_at,
            ),
        )

    def _question_asked(
        self,
        source: finish_dependencies.translator_state_models.RecordSource,
        record: dependencies.record_canonical_namespaces.record_interaction_records.AskRecord,
    ) -> dependencies.translator_type_dependencies.event_base.CanonicalEvent[
        dependencies.translator_type_dependencies.event_base.EventPayload
    ]:
        call_id = dependencies.translator_id_dependencies.ids_session_types.CodexCallId(
            record.call_id or source.native_identity,
        )
        self._call_records[source.source_key, call_id] = record
        attention_id = dependencies.translator_id_dependencies.ids_conversation.attention_id_from_codex_call(call_id)
        payload = dependencies.translator_domain_values.event_work.QuestionAsked(
            attention_id,
            finish_dependencies.translator_selection_events.attention_prompts(record),
        )
        return dependencies.translator_codex_dependencies.support.event(
            source.raw_event,
            dependencies.translator_service_dependencies.CanonicalEventDraft(
                finish_dependencies.translator_core_values.QUESTION_SUBJECT,
                str(attention_id),
                "asked",
                payload,
                occurred_at=source.occurred_at,
            ),
        )

    def _translate_record(
        self,
        raw_event: dependencies.translator_service_dependencies.raw_events.RawEvent,
        rollout_observation: dependencies.record_payload_namespaces.record_rollout_headers.RolloutObservation,
        record: dependencies.record_canonical_namespaces.record_terminal_records.RolloutRecord,
    ) -> list[
        dependencies.translator_type_dependencies.event_base.CanonicalEvent[
            dependencies.translator_type_dependencies.event_base.EventPayload
        ]
    ]:
        source = self._record_source(raw_event, rollout_observation, record)
        translator = typing.cast("_CodexSelectionTranslator", self)
        translated = self._translate_conversation_record(source, record)
        if translated is not None:
            return translated
        translated = translator._translate_tool_record(  # noqa: SLF001 -- The translator is a typed view of self.
            source,
            rollout_observation,
            record,
        )
        if translated is not None:
            return translated
        return translator._translate_tail_record(source, record)  # noqa: SLF001 -- The translator is a typed view of self.

    def _translate_conversation_record(
        self,
        source: finish_dependencies.translator_state_models.RecordSource,
        record: dependencies.record_canonical_namespaces.record_terminal_records.RolloutRecord,
    ) -> (
        list[
            dependencies.translator_type_dependencies.event_base.CanonicalEvent[
                dependencies.translator_type_dependencies.event_base.EventPayload
            ]
        ]
        | None
    ):
        translator = typing.cast("_CodexSelectionTranslator", self)
        translated = translator._translate_turn_record(source, record)  # noqa: SLF001 -- The translator is a typed view of self.
        if translated is not None:
            return translated
        translated = translator._translate_conversation_content(source, record)  # noqa: SLF001 -- The translator is a typed view of self.
        if translated is not None:
            return translated
        return translator._translate_actor_goal_record(source, record)  # noqa: SLF001 -- The translator is a typed view of self.


class _CodexSelectionTranslator(_CodexCompletionTranslator):
    """Translate model, effort, and attention selection."""

    def _translate_turn_record(
        self,
        source: finish_dependencies.translator_state_models.RecordSource,
        record: dependencies.record_canonical_namespaces.record_terminal_records.RolloutRecord,
    ) -> (
        list[
            dependencies.translator_type_dependencies.event_base.CanonicalEvent[
                dependencies.translator_type_dependencies.event_base.EventPayload
            ]
        ]
        | None
    ):
        if isinstance(record, dependencies.record_canonical_namespaces.record_task_records.TaskStartedRecord):
            return self._task_started(source, record)
        if isinstance(record, dependencies.record_canonical_namespaces.record_task_records.TaskCompleteRecord):
            return self._task_completed(source, record)
        if isinstance(record, dependencies.record_canonical_namespaces.record_task_records.TurnAbortedRecord):
            return self._turn_aborted(source, record)
        return None

    def _translate_conversation_content(
        self,
        source: finish_dependencies.translator_state_models.RecordSource,
        record: dependencies.record_canonical_namespaces.record_terminal_records.RolloutRecord,
    ) -> (
        list[
            dependencies.translator_type_dependencies.event_base.CanonicalEvent[
                dependencies.translator_type_dependencies.event_base.EventPayload
            ]
        ]
        | None
    ):
        if isinstance(
            record,
            (
                dependencies.record_canonical_namespaces.record_task_records.PromptRecord,
                dependencies.record_canonical_namespaces.record_task_records.MessageRecord,
                dependencies.record_canonical_namespaces.record_interaction_records.ChatRecord,
            ),
        ):
            return [finish_dependencies.translator_conversation_events.conversation_event(source, record)]
        if isinstance(record, dependencies.record_canonical_namespaces.record_task_records.SkillRecord):
            return finish_dependencies.translator_conversation_events.skill_events(source, record)
        if isinstance(
            record,
            (
                dependencies.record_canonical_namespaces.record_task_records.ReasoningRecord,
                dependencies.record_canonical_namespaces.record_interaction_records.ThinkRecord,
            ),
        ):
            return [finish_dependencies.translator_actor_events.reasoning_event(source, record)]
        if isinstance(
            record,
            dependencies.record_canonical_namespaces.record_actor_records.CollaborationCallRecord,
        ):
            self._remember_collaboration_call(source, record)
            return []
        return None

    def _translate_actor_goal_record(
        self,
        source: finish_dependencies.translator_state_models.RecordSource,
        record: dependencies.record_canonical_namespaces.record_terminal_records.RolloutRecord,
    ) -> (
        list[
            dependencies.translator_type_dependencies.event_base.CanonicalEvent[
                dependencies.translator_type_dependencies.event_base.EventPayload
            ]
        ]
        | None
    ):
        if isinstance(record, dependencies.record_canonical_namespaces.record_actor_records.ActorActivityRecord):
            return self._actor_activity(source, record)
        if isinstance(record, dependencies.record_canonical_namespaces.record_actor_records.UnmappedToolRecord):
            reported_name = record.name or finish_dependencies.translator_core_values.MISSING_NATIVE_VALUE
            message = f"unmapped Codex tool: {reported_name}"
            raise dependencies.translator_service_dependencies.raw_events.UnknownRawEventError(message)
        if isinstance(record, dependencies.record_canonical_namespaces.record_actor_records.GoalRecord):
            return [self._goal_changed(source, record)]
        return None

    def _translate_tool_record(
        self,
        source: finish_dependencies.translator_state_models.RecordSource,
        rollout_observation: dependencies.record_payload_namespaces.record_rollout_headers.RolloutObservation,
        record: dependencies.record_canonical_namespaces.record_terminal_records.RolloutRecord,
    ) -> (
        list[
            dependencies.translator_type_dependencies.event_base.CanonicalEvent[
                dependencies.translator_type_dependencies.event_base.EventPayload
            ]
        ]
        | None
    ):
        translated = self._translate_primary_tool_record(source, rollout_observation, record)
        if translated is not None:
            return translated
        return self._translate_remaining_tool_record(source, record)

    def _translate_primary_tool_record(
        self,
        source: finish_dependencies.translator_state_models.RecordSource,
        rollout_observation: dependencies.record_payload_namespaces.record_rollout_headers.RolloutObservation,
        record: dependencies.record_canonical_namespaces.record_terminal_records.RolloutRecord,
    ) -> (
        list[
            dependencies.translator_type_dependencies.event_base.CanonicalEvent[
                dependencies.translator_type_dependencies.event_base.EventPayload
            ]
        ]
        | None
    ):
        if isinstance(record, dependencies.record_canonical_namespaces.record_actor_records.ToolBatchRecord):
            return self._tool_batch(source, rollout_observation, record)
        if isinstance(record, dependencies.record_canonical_namespaces.record_actor_records.GoalToolRecord):
            self._remember_goal_tool_call(source, record)
            return []
        if isinstance(record, dependencies.record_canonical_namespaces.record_actor_records.TaskListRecord):
            return self._task_list_events(source, record)
        if isinstance(
            record,
            (
                dependencies.record_canonical_namespaces.record_tool_records.ExecRecord,
                dependencies.record_canonical_namespaces.record_tool_records.ToolRecord,
            ),
        ):
            return self._call_started(source, record)
        return None

    def _translate_remaining_tool_record(
        self,
        source: finish_dependencies.translator_state_models.RecordSource,
        record: dependencies.record_canonical_namespaces.record_terminal_records.RolloutRecord,
    ) -> (
        list[
            dependencies.translator_type_dependencies.event_base.CanonicalEvent[
                dependencies.translator_type_dependencies.event_base.EventPayload
            ]
        ]
        | None
    ):
        if isinstance(record, dependencies.record_canonical_namespaces.record_tool_records.StdinRecord):
            return self._stdin_events(source, record)
        if isinstance(record, dependencies.record_canonical_namespaces.record_tool_records.ExecResultRecord):
            return self._exec_result_events(source, record)
        if isinstance(
            record,
            dependencies.record_canonical_namespaces.record_tool_records.CommandCompletedRecord,
        ):
            return self._command_completed(source, record)
        return None

    def _translate_tail_record(
        self,
        source: finish_dependencies.translator_state_models.RecordSource,
        record: dependencies.record_canonical_namespaces.record_terminal_records.RolloutRecord,
    ) -> list[
        dependencies.translator_type_dependencies.event_base.CanonicalEvent[
            dependencies.translator_type_dependencies.event_base.EventPayload
        ]
    ]:
        translated = typing.cast("_CodexRecordTailTranslator", self)._translate_resource_tail_record(source, record)  # noqa: SLF001 -- The cast still refers to self.
        if translated is not None:
            return translated
        return typing.cast("_CodexRecordTailTranslator", self)._translate_state_tail_record(source, record)  # noqa: SLF001 -- The cast still refers to self.


class _CodexRecordTailTranslator(_CodexSelectionTranslator):
    """Translate resource, state, and attention record tails."""

    def _known_call_result(
        self,
        source: finish_dependencies.translator_state_models.RecordSource,
        call_id: dependencies.translator_id_dependencies.ids_session_types.CodexCallId,
        call_record: dependencies.record_canonical_namespaces.record_tool_records.ExecRecord
        | dependencies.record_canonical_namespaces.record_tool_records.ToolRecord
        | dependencies.record_canonical_namespaces.record_actor_records.ToolBatchRecord
        | dependencies.record_canonical_namespaces.record_interaction_records.AskRecord,
        record: dependencies.record_canonical_namespaces.record_tool_records.ExecResultRecord,
    ) -> list[
        dependencies.translator_type_dependencies.event_base.CanonicalEvent[
            dependencies.translator_type_dependencies.event_base.EventPayload
        ]
    ]:
        if isinstance(call_record, dependencies.record_canonical_namespaces.record_tool_records.ToolRecord):
            return self._tool_result(source.raw_event, call_id, call_record, record, source.occurred_at)
        if isinstance(
            call_record,
            dependencies.record_canonical_namespaces.record_actor_records.ToolBatchRecord,
        ):
            return self._tool_batch_result(source, call_record, record)
        if isinstance(
            call_record,
            dependencies.record_canonical_namespaces.record_interaction_records.AskRecord,
        ):
            return finish_dependencies.translator_question_results.question_result(
                source.raw_event, call_record, record, source.occurred_at,
            )
        if finish_dependencies.translator_tool_paths.read_skill_name(call_record.cmd) is not None:
            return self._skill_result(source, call_id, call_record, record)
        return self._shell_result(source, call_id, call_record, record)

    def _translate_resource_tail_record(
        self,
        source: finish_dependencies.translator_state_models.RecordSource,
        record: dependencies.record_canonical_namespaces.record_terminal_records.RolloutRecord,
    ) -> (
        list[
            dependencies.translator_type_dependencies.event_base.CanonicalEvent[
                dependencies.translator_type_dependencies.event_base.EventPayload
            ]
        ]
        | None
    ):
        if isinstance(
            record,
            dependencies.record_canonical_namespaces.record_tool_records.McpToolCompletedRecord,
        ):
            return self._mcp_tool_completed(source, record)
        if isinstance(record, dependencies.record_canonical_namespaces.record_tool_records.SearchRecord):
            return [finish_dependencies.translator_general_events.search_event(source, record)]
        if isinstance(record, dependencies.record_canonical_namespaces.record_context_records.PatchRecord):
            return finish_dependencies.translator_general_events.patch_events(source, record)
        if isinstance(record, dependencies.record_canonical_namespaces.record_context_records.UsageRecord):
            return finish_dependencies.translator_general_events.usage_events(source, record)
        return None

    def _translate_state_tail_record(
        self,
        source: finish_dependencies.translator_state_models.RecordSource,
        record: dependencies.record_canonical_namespaces.record_terminal_records.RolloutRecord,
    ) -> list[
        dependencies.translator_type_dependencies.event_base.CanonicalEvent[
            dependencies.translator_type_dependencies.event_base.EventPayload
        ]
    ]:
        if isinstance(
            record,
            (
                dependencies.record_canonical_namespaces.record_context_records.TurnContextRecord,
                dependencies.record_canonical_namespaces.record_interaction_records.SettingsRecord,
            ),
        ):
            return self._selection_events(source, record)
        if isinstance(record, dependencies.record_canonical_namespaces.record_context_records.CompactRecord):
            return []
        if isinstance(
            record,
            dependencies.record_canonical_namespaces.record_interaction_records.CompactBoundaryRecord,
        ):
            return [self._compaction_finished(source, record)]
        return self._translate_attention_tail_record(source, record)

    def _translate_attention_tail_record(
        self,
        source: finish_dependencies.translator_state_models.RecordSource,
        record: dependencies.record_canonical_namespaces.record_terminal_records.RolloutRecord,
    ) -> list[
        dependencies.translator_type_dependencies.event_base.CanonicalEvent[
            dependencies.translator_type_dependencies.event_base.EventPayload
        ]
    ]:
        if isinstance(record, dependencies.record_canonical_namespaces.record_interaction_records.AskRecord):
            return [self._question_asked(source, record)]
        if isinstance(record, dependencies.record_canonical_namespaces.record_interaction_records.PlanRecord):
            return [finish_dependencies.translator_selection_events.plan_event(source, record)]
        return []

    def _tool_result(
        self,
        raw_event: dependencies.translator_service_dependencies.raw_events.RawEvent,
        call_id: dependencies.translator_id_dependencies.ids_session_types.CodexCallId,
        tool_record: dependencies.record_canonical_namespaces.record_tool_records.ToolRecord,
        exec_result_record: dependencies.record_canonical_namespaces.record_tool_records.ExecResultRecord,
        occurred_at: float | None,
    ) -> list[
        dependencies.translator_type_dependencies.event_base.CanonicalEvent[
            dependencies.translator_type_dependencies.event_base.EventPayload
        ]
    ]:
        """One non-shell tool call and its result, as the single fact it is.

        Both halves are here: the call's name and arguments come from the record
        that opened it, the outcome and the text from the record that closed it.

        Returns:
            Result items.

        """
        source_key = finish_dependencies.translator_started_events.source_key(raw_event)
        tool_result = finish_dependencies.translator_question_results.codex_tool_result(
            tool_record,
            exec_result_record,
            self._mcp_tool_outcomes.pop((source_key, call_id), None),
        )
        if tool_result.kind == finish_dependencies.translator_identity.CodexToolKind.IGNORED:
            return []
        self._finished_tool_calls.add(finish_dependencies.translator_identity.SourceCallKey(source_key, call_id))
        return self._tool_result_fact(raw_event, call_id, tool_result, occurred_at)

    def _tool_result_fact(
        self,
        raw_event: dependencies.translator_service_dependencies.raw_events.RawEvent,
        call_id: dependencies.translator_id_dependencies.ids_session_types.CodexCallId,
        tool_result: finish_dependencies.translator_question_results.CodexToolResult,
        occurred_at: float | None,
    ) -> list[
        dependencies.translator_type_dependencies.event_base.CanonicalEvent[
            dependencies.translator_type_dependencies.event_base.EventPayload
        ]
    ]:
        answered = (
            dependencies.translator_codex_dependencies.support.content(tool_result.output)
            if tool_result.output
            else None
        )
        if tool_result.kind == finish_dependencies.translator_identity.CodexToolKind.SEARCH:
            payload: dependencies.translator_type_dependencies.event_base.EventPayload = (
                dependencies.translator_domain_events.event_resource.SearchPerformed(
                    tool_result.native_name,
                    finish_dependencies.translator_tool_parsing.search_query(tool_result.record.args),
                    answered,
                    tool_result.outcome,
                )
            )
            return [
                dependencies.translator_codex_dependencies.support.event(
                    raw_event,
                    dependencies.translator_service_dependencies.CanonicalEventDraft(
                        "search",
                        call_id,
                        "performed",
                        payload,
                        occurred_at=occurred_at,
                    ),
                ),
            ]
        if tool_result.kind == finish_dependencies.translator_identity.CodexToolKind.WEB:
            payload = dependencies.translator_domain_events.event_resource.WebFetched(
                finish_dependencies.translator_tool_parsing.web_url(tool_result.record.args),
                answered,
                tool_result.outcome,
            )
            return [
                dependencies.translator_codex_dependencies.support.event(
                    raw_event,
                    dependencies.translator_service_dependencies.CanonicalEventDraft(
                        "web",
                        call_id,
                        "fetched",
                        payload,
                        occurred_at=occurred_at,
                    ),
                ),
            ]
        path = finish_dependencies.translator_tool_parsing.tool_path(tool_result.record.args)
        if not path:
            # No path is readable from the call, and a file fact whose path was
            # invented is worse than no fact.
            return []
        working_directory = self._working_directories.get(str(raw_event.session_id))
        path = finish_dependencies.translator_question_results.resolved_tool_path(path, working_directory)
        payload = dependencies.translator_domain_events.event_resource.FileAccessed(
            path=path,
            action=dependencies.translator_domain_values.outcomes.FileAction.READ,
            outcome=tool_result.outcome,
            content=(answered if tool_result.record.name == "mcp__node_repl__js" else None),
        )
        return [
            dependencies.translator_codex_dependencies.support.event(
                raw_event,
                dependencies.translator_service_dependencies.CanonicalEventDraft(
                    finish_dependencies.translator_core_values.FILE_SUBJECT,
                    f"{call_id}:read:{path}",
                    "accessed",
                    payload,
                    occurred_at=occurred_at,
                ),
            ),
        ]


class CodexCanonicalTranslator(_CodexRecordTailTranslator):
    """Represent the Codex canonical translator."""
