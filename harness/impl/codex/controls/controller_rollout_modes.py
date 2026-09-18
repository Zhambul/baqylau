# Copyright (c) 2026 Zhambyl Yermagambet
"""Read Codex mode changes from a rollout."""

from pydantic import ValidationError

from harness.impl.codex.canonical.record_actor_records import GoalRecord
from harness.impl.codex.canonical.record_rollout_headers import RolloutDocument
from harness.impl.codex.canonical.record_task_payloads import ThreadSettingsAppliedPayload
from harness.impl.codex.controls.controller_rollout import rollout_lines_after, rollout_records_after


def plan_mode_applied_after(path: str, position: int) -> bool:
    """Check new rollout records for Codex plan mode.

    Returns:
        True when Codex applied plan mode.

    """
    for line in rollout_lines_after(path, position):
        try:
            document = RolloutDocument[ThreadSettingsAppliedPayload].model_validate_json(line)
        except ValidationError:
            continue
        settings = document.payload.thread_settings
        mode = None if settings is None else settings.collaboration_mode
        if mode is not None and mode.mode == "plan":
            return True
    return False


def goal_set_after(path: str, position: int, objective: str) -> bool:
    """Check new rollout records for a Codex goal with one objective.

    A `/goal <objective>` command writes a `thread_goal_updated` event, not a
    user prompt with the command text, so this is how that command confirms.

    Returns:
        True when Codex recorded a goal with the objective.

    """
    return any(
        isinstance(record, GoalRecord) and record.objective == objective
        for record in rollout_records_after(path, position)
    )
