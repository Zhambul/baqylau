# Copyright (c) 2026 Zhambyl Yermagambet
"""Read Codex mode changes from a rollout."""

from pydantic import ValidationError

from harness.impl.codex.canonical.record_rollout_headers import RolloutDocument
from harness.impl.codex.canonical.record_task_payloads import ThreadSettingsAppliedPayload
from harness.impl.codex.controls.controller_rollout import rollout_lines_after


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
