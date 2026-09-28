# Copyright (c) 2026 Zhambyl Yermagambet
"""Check the signs that Codex took a submitted message."""

from __future__ import annotations

from harness.impl.codex.controls import controller_send_models as models, controller_send_operations as operations


class SendConfirmation:
    """Read the rollouts and the thread title that confirm one submission."""

    def __init__(
        self,
        rollouts: operations.source_catalog.RolloutCatalog,
        title_repository: operations.title.CodexThreadTitleRepository,
    ) -> None:
        """Keep the rollout catalog and the title store."""
        self.rollouts = rollouts
        self.titles = title_repository

    def seen(
        self,
        control_context: models.controls.ControlContext,
        window_id: models.ids.WindowId,
        send_state: operations.controller_send_state.SendState,
    ) -> bool:
        """Check each sign that Codex took the submitted message.

        Returns:
            True when one sign is present.

        """
        if _plan_mode_confirmed(window_id, send_state) or _goal_confirmed(send_state):
            return True
        return (
            self._rename_confirmed(control_context, send_state)
            or self._confirmed_prompt(send_state) is not None
            or self._rewind_confirmed(control_context, send_state)
        )

    def _rewind_confirmed(
        self,
        control_context: models.controls.ControlContext,
        send_state: operations.controller_send_state.SendState,
    ) -> bool:
        if not send_state.rewind_pending:
            return False
        return self._rewind_started(send_state.source_positions, control_context.session.source_reference)

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
    screen = send_state.driver.read_text(window_id) or ""
    if operations.controller_values.PLAN_MODE_MARKER in screen:
        return True
    return any(
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
