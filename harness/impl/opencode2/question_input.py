# Copyright (c) 2026 Zhambyl Yermagambet
"""Apply validated answer steps to the native dialog."""

import time

from harness.impl.opencode2.question_steps import AnswerStep
from harness.models import controls
from terminal.models.input import KeySendRequest, TextInputMode, TextSubmitRequest
from terminal.models.values import WindowId
from terminal.models.viewport import ScreenReadRequest

REVIEW_TIMEOUT_SECONDS = 5
SCREEN_INTERVAL_SECONDS = 0.1


def submit(
    request: controls.ControlRequest, control_context: controls.ControlContext, steps: tuple[AnswerStep, ...],
) -> controls.ControlResult:
    """Submit the answers in question order.

    Returns:
        The input result. Native events supply the recorded answers.

    """
    for step in steps:
        reason = _step(control_context, step)
        if reason is not None:
            return controls.ControlResult(request.request_id, controls.ControlAcknowledgement.REJECTED, reason)
    if len(steps) > 1 or any(answer.review for answer in steps):
        reason = _confirm(control_context)
        if reason is not None:
            return controls.ControlResult(request.request_id, controls.ControlAcknowledgement.REJECTED, reason)
    return controls.ControlResult(request.request_id, controls.ControlAcknowledgement.ACKNOWLEDGED)


def _step(control_context: controls.ControlContext, answer_step: AnswerStep) -> str | None:
    window_id = WindowId(str(control_context.terminal_window_id))
    for key in answer_step.keys:
        result = control_context.terminal.input.send_key(KeySendRequest(window_id, key))
        if not result.succeeded:
            return result.reason or "OpenCode2 answer key failed"
    if answer_step.other:
        submitted = control_context.terminal.input.submit_text(TextSubmitRequest(
            window_id, answer_step.other, TextInputMode.PASTE,
        ))
        if not submitted.succeeded:
            return submitted.reason or "OpenCode2 answer text failed"
    return None


def _confirm(control_context: controls.ControlContext) -> str | None:
    window_id = WindowId(str(control_context.terminal_window_id))
    deadline = time.monotonic() + REVIEW_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        screen = control_context.terminal.viewport.read_screen(ScreenReadRequest(window_id))
        if _review(screen.text):
            result = control_context.terminal.input.send_key(KeySendRequest(window_id, "enter"))
            return None if result.succeeded else result.reason or "OpenCode2 review was not submitted"
        time.sleep(SCREEN_INTERVAL_SECONDS)
    return "OpenCode2 answer review did not appear"


def _review(screen: str | None) -> bool:
    if not screen or "enter submit" not in screen:
        return False
    return "⇆ tab" in screen and "↑↓ select" not in screen
