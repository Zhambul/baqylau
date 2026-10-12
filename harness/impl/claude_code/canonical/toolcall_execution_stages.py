# Copyright (c) 2026 Zhambyl Yermagambet
"""Own monitor, background, and start tool-call stages."""

from __future__ import annotations

from harness.impl.claude_code.canonical import (
    toolcall_domain_dependencies as domain_dependencies,
    toolcall_runtime_dependencies as runtime_dependencies,
)
from harness.impl.claude_code.canonical.toolcall_state_stages import _ToolCallAssignments


def _direct_tool_start_events(
    raw_event: runtime_dependencies.raw_events.RawEvent,
    call_id: runtime_dependencies.ids.ClaudeCodeCallId,
    native_name: str,
    arguments: runtime_dependencies.records.ToolArguments,
    kind: runtime_dependencies.tool_kind_values.ToolKind,
) -> list[domain_dependencies.event_base.CanonicalEvent[domain_dependencies.event_base.EventPayload]] | None:
    if kind == runtime_dependencies.tool_kind_values.ToolKind.SHELL:
        return [runtime_dependencies.tool_start_facts.shell_started(raw_event, call_id, native_name, arguments)]
    if kind == runtime_dependencies.tool_kind_values.ToolKind.SKILL:
        return [runtime_dependencies.tool_start_facts.skill_started(raw_event, call_id, arguments)]
    if kind == runtime_dependencies.tool_kind_values.ToolKind.ASSIGNMENT:
        return [runtime_dependencies.tool_start_facts.assignment_started(raw_event, call_id, arguments)]
    if kind == runtime_dependencies.tool_kind_values.ToolKind.MESSAGE:
        return [runtime_dependencies.tool_start_facts.actor_message(raw_event, call_id, arguments)]
    return None


class _ToolCallMonitors(_ToolCallAssignments):
    """Track monitor shells and event ordinals."""

    def monitor_armed(
        self,
        raw_event: runtime_dependencies.raw_events.RawEvent,
        task_id: runtime_dependencies.ids.ClaudeCodeShellId,
        shell_id: domain_dependencies.ids.ShellId,
    ) -> None:
        """Return the monitor armed."""
        key = raw_event.session_id, task_id
        existing = self.monitors.get(key)
        if existing is not None and existing.shell_id == shell_id:
            return
        self.monitors[key] = runtime_dependencies.tool_state_models.MonitorState(
            raw_event.session_id,
            task_id,
            shell_id,
        )

    def monitor_shell(
        self,
        raw_event: runtime_dependencies.raw_events.RawEvent,
        task_id: runtime_dependencies.ids.ClaudeCodeShellId,
    ) -> domain_dependencies.ids.ShellId | None:
        """Return the monitor shell.

        Returns:
            Monitor shell.

        """
        monitor = self.monitors.get((raw_event.session_id, task_id))
        return monitor.shell_id if monitor else None

    def next_monitor_ordinal(
        self,
        raw_event: runtime_dependencies.raw_events.RawEvent,
        task_id: runtime_dependencies.ids.ClaudeCodeShellId,
    ) -> int:
        """Return the next monitor ordinal.

        The position of the next event of this monitor, counted from zero.

                Part of the event's identity, not decoration: `stable_event_id` is built
                from the subject and the phase, so two events of one monitor recorded
                under the same phase would collapse into one row (measured — six ticks
                became one canonical event that way).

        Returns:
            Next monitor ordinal.

        """
        monitor = self.monitors.get((raw_event.session_id, task_id))
        if monitor is None:
            return 0
        ordinal = monitor.event_count
        monitor.event_count += 1
        return ordinal

    def monitor_finished(
        self, raw_event: runtime_dependencies.raw_events.RawEvent, shell_id: domain_dependencies.ids.ShellId,
    ) -> None:
        """Return the monitor finished."""
        for key, monitor in tuple(self.monitors.items()):
            if key[0] == raw_event.session_id and monitor.shell_id == shell_id:
                self.monitors.pop(key, None)


class _ToolCallBackgroundTasks(_ToolCallMonitors):
    """Track background shell tasks."""

    def background_launched(
        self,
        raw_event: runtime_dependencies.raw_events.RawEvent,
        task_id: runtime_dependencies.ids.ClaudeCodeShellId,
        shell_id: domain_dependencies.ids.ShellId,
    ) -> None:
        """Return the background launched."""
        self.background_tasks[raw_event.session_id, task_id] = (
            runtime_dependencies.tool_state_models.BackgroundTaskState(
                raw_event.session_id,
                task_id,
                shell_id,
            )
        )

    def background_stopped(
        self,
        raw_event: runtime_dependencies.raw_events.RawEvent,
        task_id: runtime_dependencies.ids.ClaudeCodeShellId,
        transcript_path: str,
    ) -> list[domain_dependencies.event_base.CanonicalEvent[domain_dependencies.event_base.EventPayload]]:
        """Close a background Bash command or a Monitor after a successful TaskStop.

        Returns:
            Result items.

        """
        if not task_id:
            return []
        shell_id = self._stopped_shell(raw_event, task_id, transcript_path)
        if shell_id is None:
            return []
        return [
            runtime_dependencies.support.event(
                raw_event,
                runtime_dependencies.support.CanonicalEventDraft(
                    runtime_dependencies.tool_kind_values.ToolKind.SHELL.value,
                    str(shell_id),
                    "output_finished",
                    domain_dependencies.event_shell.ShellOutputFinished(
                        shell_id, domain_dependencies.outcomes.Outcome.CANCELLED,
                    ),
                ),
            ),
        ]

    def _stopped_shell(
        self,
        raw_event: runtime_dependencies.raw_events.RawEvent,
        task_id: runtime_dependencies.ids.ClaudeCodeShellId,
        transcript_path: str,
    ) -> domain_dependencies.ids.ShellId | None:
        background = self.background_tasks.pop((raw_event.session_id, task_id), None)
        if background is not None:
            return background.shell_id
        # A stopped Monitor sends no end notification (measured in claude-code
        # 2.1.284), so this stop is its only end. The arm stays: an event queued
        # before the stop can reach the transcript after the stop's hook.
        monitor_shell = self.monitor_shell(raw_event, task_id)
        if monitor_shell is not None:
            return monitor_shell
        call_id = runtime_dependencies.transcript.background_call(transcript_path, task_id)
        return None if call_id is None else runtime_dependencies.ids.shell_id_from_claude_code_call(call_id)


class _ToolCallStarts(_ToolCallBackgroundTasks):
    """Translate native tool start events."""

    def tool_started(
        self,
        raw_event: runtime_dependencies.raw_events.RawEvent,
        tool_call_native: runtime_dependencies.records.ToolCallNative,
    ) -> list[domain_dependencies.event_base.CanonicalEvent[domain_dependencies.event_base.EventPayload]]:
        """Return the tool started.

        Returns:
            Tool started.

        """
        call_id = runtime_dependencies.ids.ClaudeCodeCallId(
            str(
                tool_call_native.tool_use_id or tool_call_native.id or raw_event.source_position,
            ),
        )
        native_name = str(tool_call_native.tool_name or tool_call_native.name or "tool")
        arguments = tool_call_native.input if tool_call_native.tool_input is None else tool_call_native.tool_input
        arguments = runtime_dependencies.records.ToolArguments() if arguments is None else arguments
        self.remember(raw_event, call_id, native_name, arguments)
        return self._tool_start_events(
            raw_event,
            call_id,
            native_name,
            arguments,
        )

    # --- the result ----------------------------------------------------------

    def _tool_start_events(
        self,
        raw_event: runtime_dependencies.raw_events.RawEvent,
        call_id: runtime_dependencies.ids.ClaudeCodeCallId,
        native_name: str,
        arguments: runtime_dependencies.records.ToolArguments,
    ) -> list[domain_dependencies.event_base.CanonicalEvent[domain_dependencies.event_base.EventPayload]]:
        kind = runtime_dependencies.tool_classification.tool_kind(native_name)
        direct_events = _direct_tool_start_events(raw_event, call_id, native_name, arguments, kind)
        if direct_events is not None:
            return direct_events
        return self._attention_tool_start_events(raw_event, call_id, arguments, kind)

    def _attention_tool_start_events(
        self,
        raw_event: runtime_dependencies.raw_events.RawEvent,
        call_id: runtime_dependencies.ids.ClaudeCodeCallId,
        arguments: runtime_dependencies.records.ToolArguments,
        kind: runtime_dependencies.tool_kind_values.ToolKind,
    ) -> list[domain_dependencies.event_base.CanonicalEvent[domain_dependencies.event_base.EventPayload]]:
        if kind == runtime_dependencies.tool_kind_values.ToolKind.QUESTION:
            attention_id = runtime_dependencies.ids.attention_id_from_claude_code_call(call_id)
            payload: domain_dependencies.event_base.EventPayload = domain_dependencies.event_work.QuestionAsked(
                attention_id,
                runtime_dependencies.tool_attention.questions(arguments),
            )
            return [
                runtime_dependencies.support.event(
                    raw_event,
                    runtime_dependencies.support.CanonicalEventDraft(
                        runtime_dependencies.tool_kind_values.ToolKind.QUESTION.value,
                        str(attention_id),
                        "asked",
                        payload,
                    ),
                ),
            ]
        if kind == runtime_dependencies.tool_kind_values.ToolKind.PLAN:
            # Claude Code 2.1.294 writes `ExitPlanMode` with no arguments in the
            # transcript and gives the plan text only to the PreToolUse hook. The
            # first writer of a fact is kept, so an empty plan from the transcript
            # hid the hook's plan. A call without plan text makes no fact.
            if not arguments.plan:
                return []
            attention_id = runtime_dependencies.ids.attention_id_from_claude_code_call(call_id)
            payload = domain_dependencies.event_work.PlanProposed(
                attention_id, runtime_dependencies.support.content(arguments.plan or "", markdown=True),
            )
            return [
                runtime_dependencies.support.event(
                    raw_event,
                    runtime_dependencies.support.CanonicalEventDraft(
                        runtime_dependencies.tool_kind_values.ToolKind.PLAN.value,
                        str(attention_id),
                        "proposed",
                        payload,
                    ),
                ),
            ]
        # file, search, web, worktree: nothing is known yet that is worth a
        # fact. `ignored`: nothing ever will be.
        return []
