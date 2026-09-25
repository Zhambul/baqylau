# Copyright (c) 2026 Zhambyl Yermagambet
"""Own Codex control send."""

from __future__ import annotations

from harness.impl.codex.controls import controller_send_models as models, controller_send_operations as operations

SEND_TEXT_REQUEST_REQUIRED = "send_text handler requires SendText"


class SendTextHandler(models.contract.ControlHandler):
    """Represent send text handler."""

    def __init__(
        self,
        harness_runtime_config: models.runtime.HarnessRuntimeConfig,
        rewind_continuity: models.continuity.RewindContinuity,
        title_repository: operations.title.CodexThreadTitleRepository,
    ) -> None:
        """Initialize the object."""
        self.runtime = harness_runtime_config
        self.rewind_continuity = rewind_continuity
        self.titles = title_repository
        self.rollouts = operations.source_catalog.RolloutCatalog(
            str(harness_runtime_config.configuration_directory),
        )

    def __call__(
        self,
        request: models.controls.ControlRequest,
        control_context: models.controls.ControlContext,
    ) -> models.controls.ControlResult | models.controls.MessageDeliveryResult:
        """Handle a send-text request.

        Returns:
            The call.

        Raises:
            TypeError: If an input has an invalid type.

        """
        if not isinstance(request, models.controls.SendText):
            raise TypeError(SEND_TEXT_REQUEST_REQUIRED)
        window_id = control_context.terminal_window_id
        if not operations.controller_results.has_live_window(window_id):
            return operations.controller_results.session_not_live(request)
        if control_context.lead_active:
            return operations.controller_send_state.queue_active_message(request, control_context)
        terminal_driver = models.terminal_driver.TerminalDriver(control_context.terminal)
        clear_error = operations.controller_send_state.clear_composer(terminal_driver, window_id)
        if clear_error is not None:
            return models.controls.ControlResult(
                request.request_id,
                models.controls.ControlAcknowledgement.REJECTED,
                clear_error,
            )
        send_state = self._send_state(
            request,
            control_context,
            window_id,
            terminal_driver,
        )
        submission = operations.controller_send_state.submit_message(request, control_context, window_id, send_state)
        if submission.status != models.controls.ControlAcknowledgement.ACKNOWLEDGED:
            return models.controls.ControlResult(
                submission.request_id,
                models.controls.ControlAcknowledgement.REJECTED,
                submission.reason,
            )
        return self._wait_for_confirmation(control_context, window_id, send_state, submission.request_id)

    def _send_state(
        self,
        request: models.controls.SendText,
        control_context: models.controls.ControlContext,
        window_id: models.ids.WindowId,
        terminal_driver: models.terminal_driver.TerminalDriver,
    ) -> operations.controller_send_state.SendState:
        submitted_message = operations.controller_results.message_text(request)
        rewind_pending = self.rewind_continuity.pending(request.session_id, window_id)
        return operations.controller_send_state.SendState(
            terminal_driver,
            rewind_pending,
            operations.controller_rollout.source_positions(
                self.rollouts, control_context.session.source_reference, discover=rewind_pending,
            ),
            submitted_message,
            submitted_message.strip(),
        )

    def _wait_for_confirmation(
        self,
        control_context: models.controls.ControlContext,
        window_id: models.ids.WindowId,
        send_state: operations.controller_send_state.SendState,
        request_id: models.ids.RequestId,
    ) -> models.controls.ControlResult | models.controls.MessageDeliveryResult:
        deadline = operations.time.monotonic() + operations.controller_timeouts.SEND_CONFIRM_TIMEOUT_SECONDS
        while True:
            if self._confirmation_seen(control_context, window_id, send_state):
                return models.controls.MessageDeliveryResult(request_id, models.controls.MessageDeliveryStatus.SENT)
            if operations.time.monotonic() >= deadline:
                return models.controls.ControlResult(
                    request_id,
                    models.controls.ControlAcknowledgement.INDETERMINATE,
                    "Codex did not confirm the submitted message",
                )
            operations.time.sleep(operations.controller_timeouts.SEND_CONFIRM_POLL_SECONDS)

    def _confirmation_seen(
        self,
        control_context: models.controls.ControlContext,
        window_id: models.ids.WindowId,
        send_state: operations.controller_send_state.SendState,
    ) -> bool:
        return (
            _plan_mode_confirmed(window_id, send_state)
            or self._rename_confirmed(control_context, send_state)
            or _goal_confirmed(send_state)
            or self._confirmed_prompt(send_state) is not None
            or (
                send_state.rewind_pending
                and self._rewind_started(send_state.source_positions, control_context.session.source_reference)
            )
        )

    def _rename_confirmed(
        self,
        control_context: models.controls.ControlContext,
        send_state: operations.controller_send_state.SendState,
    ) -> bool:
        renamed_to = operations.controller_rollout.command_argument(
            send_state.expected_message,
            operations.controller_values.RENAME_COMMAND_PREFIX,
        )
        if renamed_to is None:
            return False
        observed_title = self.titles.read_title(control_context.session.source_reference)
        return observed_title is not None and observed_title.text == renamed_to

    def _confirmed_prompt(
        self,
        send_state: operations.controller_send_state.SendState,
    ) -> str | None:
        positions = send_state.source_positions
        if send_state.rewind_pending:
            known = {position.path for position in positions}
            positions = (*positions, *(
                operations.controller_results.RolloutPosition(path, 0)
                for path in self.rollouts.paths() if path not in known
            ))
        for position in positions:
            if operations.controller_rollout.confirmed_prompt_after(
                position.path, position.position, send_state.expected_message,
            ):
                return position.path
        return None

    def _rewind_started(
        self,
        source_positions: tuple[operations.controller_results.RolloutPosition, ...],
        source_reference: str,
    ) -> bool:
        original = operations.os.path.realpath(source_reference)
        return any(
            operations.os.path.realpath(path) != original
            and any(
                isinstance(record, models.records.TaskStartedRecord)
                for record in operations.controller_rollout.rollout_records_after(
                    path,
                    operations.controller_rollout.position_for(source_positions, path),
                )
            )
            for path in self.rollouts.paths()
        )


def _plan_mode_confirmed(
    window_id: models.ids.WindowId,
    send_state: operations.controller_send_state.SendState,
) -> bool:
    if send_state.expected_message != operations.controller_values.PLAN_COMMAND:
        return False
    return operations.controller_values.PLAN_MODE_MARKER in (send_state.driver.read_text(window_id) or "") or any(
        operations.controller_rollout_modes.plan_mode_applied_after(position.path, position.position)
        for position in send_state.source_positions
    )


def _goal_confirmed(send_state: operations.controller_send_state.SendState) -> bool:
    objective = operations.controller_rollout.command_argument(
        send_state.expected_message,
        operations.controller_values.GOAL_COMMAND_PREFIX,
    )
    if objective is None:
        return False
    return any(
        operations.controller_rollout_modes.goal_set_after(position.path, position.position, objective)
        for position in send_state.source_positions
    )
