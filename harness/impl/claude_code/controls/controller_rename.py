# Copyright (c) 2026 Zhambyl Yermagambet
"""Own Claude Code session renaming."""

import pathlib
import time

from pydantic import ValidationError

from harness.contract import ControlHandler
from harness.impl.claude_code import probe
from harness.impl.claude_code.canonical import records, transcript_titles as transcript
from harness.impl.claude_code.controls import controller_commands as commands, controller_values as control_values
from harness.models import controls as control_models
from harness.services import composer as composer_service
from harness.services.terminal_driver import TerminalDriver


def _title_record(source_reference: str, after_position: int, expected: str | None) -> bool:
    try:
        with pathlib.Path(source_reference).open("rb") as source:
            source.seek(max(after_position, 0))
            lines = source.read().splitlines()
    except OSError:
        return False
    for line in lines:
        record = _title_line(line)
        if record is not None and _title_matches(record, expected):
            return True
    return False


def _title_line(line: bytes) -> records.TitleRecord | None:
    try:
        return records.TitleRecord.model_validate_json(line)
    except ValidationError:
        return None


def _title_matches(record: records.TitleRecord, expected: str | None) -> bool:
    title = (record.agent_name or "").strip()
    expected_title = expected is None or title == expected
    return record.type == "agent-name" and expected_title


def _rename_command(
    request: control_models.ControlRequest,
    control_context: control_models.ControlContext,
    command: str,
    expected: str | None,
) -> control_models.ControlResult:
    source_reference = control_context.session.source_reference
    try:
        position = pathlib.Path(source_reference).stat().st_size
    except OSError:
        position = -1
    result = commands.send_command(request, control_context, command)
    if result.status != control_models.ControlAcknowledgement.ACKNOWLEDGED:
        return result
    deadline = time.monotonic() + control_values.NATIVE_TITLE_CONFIRM_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if position >= 0 and _title_record(source_reference, position, expected):
            return result
        time.sleep(control_values.NATIVE_TEXT_CONFIRM_POLL_SECONDS)
    return control_models.ControlResult(
        request.request_id,
        control_models.ControlAcknowledgement.INDETERMINATE,
        "Claude Code did not confirm the title",
    )


def _rename_with_preserved_draft(
    request: control_models.ControlRequest,
    control_context: control_models.ControlContext,
    command: str,
    expected: str | None,
) -> control_models.ControlResult:
    window_id = control_context.terminal_window_id
    if not commands.has_live_window(window_id):
        return control_models.ControlResult(
            request.request_id,
            control_models.ControlAcknowledgement.REJECTED,
            control_values.SESSION_NOT_LIVE_REASON,
        )
    driver = TerminalDriver(control_context.terminal)
    try:
        return composer_service.with_preserved_draft(
            probe.ClaudeCodeComposer(),
            driver,
            window_id,
            lambda: _rename_command(request, control_context, command, expected),
        )
    except composer_service.ComposerRestoreError as error:
        return control_models.ControlResult(
            request.request_id,
            control_models.ControlAcknowledgement.INDETERMINATE,
            str(error),
        )
    except probe.ComposerError as error:
        # The draft could not be cleared, so the rename was not typed.
        return control_models.ControlResult(
            request.request_id,
            control_models.ControlAcknowledgement.REJECTED,
            str(error),
        )


class RenameSessionHandler(ControlHandler):
    """Rename a Claude Code session."""

    def __call__(
        self,
        request: control_models.ControlRequest,
        control_context: control_models.ControlContext,
    ) -> control_models.ControlResult:
        """Handle a session-rename request.

        Returns:
            The control result.

        Raises:
            TypeError: If an input has an invalid type.

        """
        if not isinstance(request, control_models.RenameSession):
            msg = "rename_session handler requires RenameSession"
            raise TypeError(msg)
        session = control_context.session
        if control_context.terminal_window_id is None:
            outcome = transcript.titles.set_title(session.source_reference, request.name)
            if outcome == "unsupported":
                return control_models.ControlResult(
                    request.request_id,
                    control_models.ControlAcknowledgement.REJECTED,
                    "session source is not renameable",
                )
            if outcome == "unavailable":
                return control_models.ControlResult(
                    request.request_id,
                    control_models.ControlAcknowledgement.INDETERMINATE,
                    "native title store is unavailable",
                )
            return control_models.DurableTitleResult(
                request.request_id,
                control_models.ControlAcknowledgement.ACKNOWLEDGED,
            )
        return _rename_with_preserved_draft(request, control_context, f"/rename {request.name}", request.name)


class AutoNameSessionHandler(ControlHandler):
    """Ask Claude Code to name a session."""

    def __call__(
        self,
        request: control_models.ControlRequest,
        control_context: control_models.ControlContext,
    ) -> control_models.ControlResult:
        """Handle an automatic-name request.

        Returns:
            The control result.

        Raises:
            TypeError: If an input has an invalid type.

        """
        if not isinstance(request, control_models.AutoNameSession):
            msg = "auto_name_session handler requires AutoNameSession"
            raise TypeError(msg)
        return _rename_with_preserved_draft(request, control_context, "/rename", None)
