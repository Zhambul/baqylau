# Copyright (c) 2026 Zhambyl Yermagambet
"""Own finish and attention tool-call stages."""

from __future__ import annotations

from harness.impl.claude_code.canonical import (
    toolcall_domain_dependencies as domain_dependencies,
    toolcall_finish_dependencies as finish_dependencies,
    toolcall_runtime_dependencies as runtime_dependencies,
)
from harness.impl.claude_code.canonical.toolcall_execution_stages import _ToolCallStarts


class _ToolCallFinishes(_ToolCallStarts):
    """Translate completed tool call events."""

    def tool_finished(
        self,
        raw_event: runtime_dependencies.raw_events.RawEvent,
        tool_call_native: runtime_dependencies.records.ToolCallNative,
        *,
        failed: bool,
        result: domain_dependencies.content.Content | None = None,
        cancelled: bool = False,
    ) -> list[domain_dependencies.event_base.CanonicalEvent[domain_dependencies.event_base.EventPayload]]:
        """Everything one tool call's RESULT says.

        `result` is the runtime_dependencies.transcript's own text of the answer, when that is where
        this observation came from; the hook path leaves it None and the native
        response document below stands in for it. Both spellings of one answer
        converge on one fact.

        Returns:
            Result items.

        """
        call_id = runtime_dependencies.ids.ClaudeCodeCallId(
            str(tool_call_native.tool_use_id or tool_call_native.id or raw_event.source_position),
        )
        native_name, arguments = self.recall(
            raw_event,
            call_id,
            tool_call_native.tool_name or None,
            tool_call_native.tool_input,
        )
        identity = finish_dependencies.tool_result_models.FinishedToolIdentity(
            call_id, native_name, arguments, runtime_dependencies.tool_classification.tool_kind(native_name),
        )
        if identity.kind in {
            runtime_dependencies.tool_kind_values.ToolKind.IGNORED,
            runtime_dependencies.tool_kind_values.ToolKind.MESSAGE,
        }:
            return []
        finished = finish_dependencies.tool_finished_answers.finished_tool_result(
            tool_call_native.tool_response,
            identity,
            result,
            failed=failed,
            cancelled=cancelled,
        )
        if identity.kind == runtime_dependencies.tool_kind_values.ToolKind.SHELL:
            return self._shell_finished(
                raw_event,
                identity,
                finished,
            )
        if identity.kind == runtime_dependencies.tool_kind_values.ToolKind.ASSIGNMENT:
            return self._assignment_finished(
                raw_event,
                call_id,
                finished.response,
                finished.outcome,
            )
        return finish_dependencies.tool_completion_facts.finished_tool_facts(raw_event, identity, finished)

    def tool_result(
        self,
        raw_event: runtime_dependencies.raw_events.RawEvent,
        transcript_result: finish_dependencies.tool_result_models.TranscriptToolResult,
    ) -> list[domain_dependencies.event_base.CanonicalEvent[domain_dependencies.event_base.EventPayload]]:
        """One tool_result block from the runtime_dependencies.transcript, as facts.

        The runtime_dependencies.transcript names no tool and carries no input. Recover these fields
        from the earlier tool-use record when the daemon restarted mid-call.
        The hook delivery of the same result stands on its own and converges on
        the same event ids.

        Returns:
            Result items.

        """
        if not self.known(raw_event, transcript_result.call_id):
            recovered = runtime_dependencies.transcript.tool_call_before(
                raw_event.source_name,
                raw_event.source_position,
                transcript_result.call_id,
            )
            if recovered is None:
                return []
            self.remember(raw_event, transcript_result.call_id, *recovered)
        native_name = self.recall(raw_event, transcript_result.call_id, None, None)[0]
        kind = runtime_dependencies.tool_classification.tool_kind(native_name)
        if kind == runtime_dependencies.tool_kind_values.ToolKind.QUESTION and (
            transcript_result.failed
            or not isinstance(transcript_result.tool_response, runtime_dependencies.records.ToolResponse)
            or transcript_result.tool_response.answers is None
        ):
            return []
        if kind not in runtime_dependencies.tool_kind_values.TRANSCRIPT_RESULT_KINDS:
            return []
        events: list[domain_dependencies.event_base.CanonicalEvent[domain_dependencies.event_base.EventPayload]] = []
        if kind == runtime_dependencies.tool_kind_values.ToolKind.SHELL:
            shell_id = runtime_dependencies.ids.shell_id_from_claude_code_call(transcript_result.call_id)
            # REPLACE, and ordinal zero: this is the whole output as the harness
            # recorded it, not one more slice of a file being followed.
            events.append(
                runtime_dependencies.support.event(
                    raw_event,
                    runtime_dependencies.support.CanonicalEventDraft(
                        runtime_dependencies.tool_kind_values.ToolKind.SHELL.value,
                        str(shell_id),
                        "progress:0",
                        domain_dependencies.event_shell.ShellProgressed(
                            shell_id,
                            0,
                            domain_dependencies.outcomes.ProgressStream.OUTPUT,
                            runtime_dependencies.support.content(transcript_result.result_text),
                            domain_dependencies.outcomes.OutputMode.REPLACE,
                        ),
                    ),
                ),
            )
        events.extend(
            self.tool_finished(
                raw_event,
                runtime_dependencies.records.ToolCallNative(
                    tool_use_id=transcript_result.call_id,
                    tool_response=transcript_result.tool_response,
                ),
                failed=transcript_result.failed,
                result=runtime_dependencies.support.content(transcript_result.result_text),
                cancelled=transcript_result.cancelled,
            ),
        )
        return events

    def _shell_finished(
        self,
        raw_event: runtime_dependencies.raw_events.RawEvent,
        identity: finish_dependencies.tool_result_models.FinishedToolIdentity,
        finished: finish_dependencies.tool_result_models.FinishedToolResult,
    ) -> list[domain_dependencies.event_base.CanonicalEvent[domain_dependencies.event_base.EventPayload]]:
        shell_id = runtime_dependencies.ids.shell_id_from_claude_code_call(identity.call_id)
        events: list[domain_dependencies.event_base.CanonicalEvent[domain_dependencies.event_base.EventPayload]] = []
        # BACKGROUNDED MID-RUN (ctrl+b on a running command). Structural, from the
        # one document that holds both halves: the input never asked to run in the
        # background, and the response carries a background task id anyway. The
        # stub in the runtime_dependencies.transcript's tool_result says the same thing in prose, but its
        # message id belongs to a namespace the runtime_dependencies.transcript never uses again.
        #
        # NOT keyed on the response's `backgroundedByUser` flag, though it is right
        # there beside the task id (measured: `{"backgroundTaskId":"b18ibyhwf",
        # "backgroundedByUser":true}`). The flag answers WHO moved it, and the
        # harness can move a command itself — `isAutobackgroundingAllowed` decides
        # when — which is the same fact about the command arriving with the flag
        # false. What matters here is that it moved.
        #
        # BEFORE the finish below, deliberately: the follow of the file this
        # command is still writing to is ended by `shell.finished` unless this
        # fact has already re-armed it (see domain_dependencies.event_shell.ShellBackgrounded).
        background_task_id = runtime_dependencies.ids.ClaudeCodeShellId(
            str(finished.response.background_task_id or ""),
        )
        if background_task_id:
            self.background_launched(raw_event, background_task_id, shell_id)
        if background_task_id and not identity.arguments.run_in_background:
            events.append(
                runtime_dependencies.support.event(
                    raw_event,
                    runtime_dependencies.support.CanonicalEventDraft(
                        runtime_dependencies.tool_kind_values.ToolKind.SHELL.value,
                        str(shell_id),
                        "backgrounded",
                        domain_dependencies.event_shell.ShellBackgrounded(shell_id),
                    ),
                ),
            )
        # An armed Monitor names its task id here and nowhere else this
        # translation can see it. The `shell.finished` below is the ARM
        # returning, not the watch ending — the watch runs on, and its own end
        # arrives as a notification (see monitor_armed).
        monitor_task_id = runtime_dependencies.ids.ClaudeCodeShellId("")
        if identity.native_name == runtime_dependencies.tool_kind_values.MONITOR_TOOL_NAME:
            monitor_task_id = runtime_dependencies.ids.ClaudeCodeShellId(
                str(finished.response.task_id or ""),
            )
            if monitor_task_id:
                self.monitor_armed(raw_event, monitor_task_id, shell_id)
        exit_state = finish_dependencies.tool_shell_exit.shell_exit(finished.answer, finished.outcome)
        events.append(
            runtime_dependencies.support.event(
                raw_event,
                runtime_dependencies.support.CanonicalEventDraft(
                    runtime_dependencies.tool_kind_values.ToolKind.SHELL.value,
                    str(shell_id),
                    runtime_dependencies.tool_values.FINISHED_PHASE,
                    domain_dependencies.event_shell.ShellFinished(
                        shell_id,
                        exit_state.outcome,
                        None,
                        exit_state.code,
                    ),
                ),
            ),
        )
        if (
            (
                identity.native_name == runtime_dependencies.tool_kind_values.MONITOR_TOOL_NAME
                and not monitor_task_id
            )
            or (identity.arguments.run_in_background and not background_task_id)
        ):
            # A rejected asynchronous start has no native task. It cannot send
            # a later end notification, so its tool result ends its output.
            events.append(
                runtime_dependencies.support.event(
                    raw_event,
                    runtime_dependencies.support.CanonicalEventDraft(
                        runtime_dependencies.tool_kind_values.ToolKind.SHELL.value,
                        str(shell_id),
                        "output_finished",
                        domain_dependencies.event_shell.ShellOutputFinished(shell_id, exit_state.outcome),
                    ),
                ),
            )
        return events


class _ToolCallAttention(_ToolCallFinishes):
    """Translate pending and declined attention."""

    def pending_attention(
        self,
        raw_event: runtime_dependencies.raw_events.RawEvent,
        call_id: runtime_dependencies.ids.ClaudeCodeCallId,
    ) -> bool:
        """Whether this call was one that asks a person something.

        Returns:
            Whether this call was one that asks a person something.

        """
        if not self.known(raw_event, call_id):
            return False
        native_name, _arguments = self.recall(raw_event, call_id, None, None)
        return runtime_dependencies.tool_classification.tool_kind(native_name) in {
            runtime_dependencies.tool_kind_values.ToolKind.QUESTION,
            runtime_dependencies.tool_kind_values.ToolKind.PLAN,
        }

    def attention_declined(
        self,
        raw_event: runtime_dependencies.raw_events.RawEvent,
        call_id: runtime_dependencies.ids.ClaudeCodeCallId,
        result_text: str,
    ) -> domain_dependencies.event_base.CanonicalEvent[domain_dependencies.event_base.EventPayload]:
        """Return the attention declined.

        The resolution of an attention the user REFUSED. A refused tool call never
                runs, so Claude Code fires no PostToolUse and `tool_finished` — the only other
                emitter — never sees it; the runtime_dependencies.transcript's tool_result is the sole raw event the
                request ended. It names no tool, hence the remembered call. Refusal carries
                no answers, so nothing is lost if the hook path also reports the same fact:
                both derive the resolution from the same text and converge on one event.

        Returns:
            Attention declined.

        """
        attention_id = runtime_dependencies.ids.attention_id_from_claude_code_call(call_id)
        native_name = self.recall(raw_event, call_id, None, None)[0]
        if native_name == "AskUserQuestion":
            payload: domain_dependencies.event_base.EventPayload = domain_dependencies.event_work.QuestionAnswered(
                attention_id, (), None,
            )
            return runtime_dependencies.support.event(
                raw_event,
                runtime_dependencies.support.CanonicalEventDraft(
                    runtime_dependencies.tool_kind_values.ToolKind.QUESTION.value,
                    str(attention_id),
                    "answered",
                    payload,
                ),
            )
        resolution = finish_dependencies.tool_attention.plan_resolution(result_text, failed=True)
        payload = domain_dependencies.event_work.PlanResolved(attention_id, *resolution)
        return runtime_dependencies.support.event(
            raw_event,
            runtime_dependencies.support.CanonicalEventDraft(
                runtime_dependencies.tool_kind_values.ToolKind.PLAN.value,
                str(attention_id),
                finish_dependencies.raw_event_builders.plan_resolution_phase(payload),
                payload,
            ),
        )


class ToolCallSemantics(_ToolCallAttention):
    """Join related native tool-call events for one session."""
