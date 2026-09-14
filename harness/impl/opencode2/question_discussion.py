# Copyright (c) 2026 Zhambyl Yermagambet
"""Dismiss the native question before sending discussion text."""

import time

from harness.models import controls
from terminal.models.input import KeySendRequest, TextInputMode, TextSubmitRequest
from terminal.models.values import WindowId
from terminal.models.viewport import ScreenReadRequest

DIALOG_TIMEOUT_SECONDS = 5
SCREEN_INTERVAL_SECONDS = 0.1


def discuss(
    answer_question: controls.AnswerQuestion, control_context: controls.ControlContext,
) -> controls.ControlResult:
    """Dismiss the question and submit the requested chat text.

    Returns:
        The input result after the dialog closes.

    """
    window_id = WindowId(str(control_context.terminal_window_id))
    closed = control_context.terminal.input.send_key(KeySendRequest(window_id, "escape"))
    if not closed.succeeded or not _closed(control_context, window_id):
        return _failed(answer_question, closed.reason or "OpenCode2 question did not close")
    if not answer_question.discussion:
        return controls.ControlResult(answer_question.request_id, controls.ControlAcknowledgement.ACKNOWLEDGED)
    sent = control_context.terminal.input.submit_text(TextSubmitRequest(
        window_id, answer_question.discussion, TextInputMode.PASTE,
    ))
    if not sent.succeeded:
        return _failed(answer_question, sent.reason or "discussion was not submitted")
    return controls.ControlResult(answer_question.request_id, controls.ControlAcknowledgement.ACKNOWLEDGED)


def _closed(control_context: controls.ControlContext, window_id: WindowId) -> bool:
    deadline = time.monotonic() + DIALOG_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        screen = control_context.terminal.viewport.read_screen(ScreenReadRequest(window_id))
        if screen.succeeded and screen.text and "esc dismiss" not in screen.text:
            return True
        time.sleep(SCREEN_INTERVAL_SECONDS)
    return False


def _failed(answer_question: controls.AnswerQuestion, reason: str) -> controls.ControlResult:
    return controls.ControlResult(answer_question.request_id, controls.ControlAcknowledgement.REJECTED, reason)
