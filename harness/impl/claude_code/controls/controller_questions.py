# Copyright (c) 2026 Zhambyl Yermagambet
"""Own Claude Code question controls."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import TypeAdapter, ValidationError

from domain.event_work import QuestionAsked
from harness.contract import ControlHandler
from harness.impl.claude_code.canonical import records
from harness.impl.claude_code.controls import ask_models, askdialog, controller_values as control_values
from harness.impl.claude_code.controls.controller_send import deliver_native_text
from harness.models import controls as control_models
from harness.services.terminal_driver import TerminalDriver

if TYPE_CHECKING:
    from domain.ids import WindowId


def _native_prompts(question_asked: QuestionAsked) -> list[records.Question]:
    return [
        records.Question(
            id=prompt.prompt_id,
            header=prompt.title or "",
            question=prompt.prompt,
            multi_select=prompt.multiple,
            options=[
                records.QuestionOption(label=choice.label, description=choice.description or "")
                for choice in prompt.choices
            ],
        )
        for prompt in question_asked.questions
    ]


def _typed_answer_refusal(question_asked: QuestionAsked, answers: list[ask_models.AnswerDraft]) -> str | None:
    """Refuse a typed answer before the dialog is touched.

    Returns:
        The refusal reason, or None when each typed answer has a row.

    """
    for prompt, answer in zip(question_asked.questions, answers, strict=False):
        if not prompt.free_text and (answer.other or "").strip():
            title = prompt.title or prompt.prompt
            return f"question {title!r} accepts no typed answer; select an option or use Chat about this"
    return None


class AnswerQuestionHandler(ControlHandler):
    """Answer a Claude Code question."""

    def __call__(
        self,
        request: control_models.ControlRequest,
        control_context: control_models.ControlContext,
    ) -> control_models.ControlResult:
        """Handle a question-answer request.

        Returns:
            The control result.

        Raises:
            TypeError: If an input has an invalid type.

        """
        if not isinstance(request, control_models.AnswerQuestion):
            msg = "answer_question handler requires AnswerQuestion"
            raise TypeError(msg)
        if not isinstance(control_context.pending_attention, QuestionAsked):
            return control_models.ControlResult(
                request.request_id,
                control_models.ControlAcknowledgement.REJECTED,
                "no question is pending",
            )
        window_id = control_context.terminal_window_id
        if window_id is None:
            return control_models.ControlResult(
                request.request_id,
                control_models.ControlAcknowledgement.REJECTED,
                control_values.SESSION_NOT_LIVE_REASON,
            )
        try:
            answers = (
                []
                if request.answers is None
                else TypeAdapter(list[ask_models.AnswerDraft]).validate_json(request.answers.json_text)
            )
        except ValidationError as error:
            return control_models.ControlResult(
                request.request_id,
                control_models.ControlAcknowledgement.REJECTED,
                f"malformed question answer: {error}",
            )
        refusal = _typed_answer_refusal(control_context.pending_attention, answers)
        if refusal is not None:
            return control_models.ControlResult(
                request.request_id,
                control_models.ControlAcknowledgement.REJECTED,
                refusal,
            )
        return _answer_question(
            request,
            control_context,
            control_context.pending_attention,
            window_id,
            answers,
        )


def _answer_question(
    request: control_models.AnswerQuestion,
    control_context: control_models.ControlContext,
    question_asked: QuestionAsked,
    window_id: WindowId,
    answers: list[ask_models.AnswerDraft],
) -> control_models.ControlResult:
    driver = TerminalDriver(control_context.terminal)
    try:
        askdialog.drive(
            driver,
            window_id,
            ask_models.AskRequest(
                _native_prompts(question_asked),
                answers,
                chat=request.decision == control_models.AnswerDecision.DISCUSS,
            ),
        )
    except ask_models.AskError as error:
        return control_models.ControlResult(
            request.request_id,
            control_models.ControlAcknowledgement.INDETERMINATE,
            str(error),
        )
    if request.decision == control_models.AnswerDecision.DISCUSS and request.discussion:
        return _deliver_question_discussion(request, control_context, driver, window_id)
    return control_models.ControlResult(request.request_id, control_models.ControlAcknowledgement.ACKNOWLEDGED)


def _deliver_question_discussion(
    request: control_models.AnswerQuestion,
    control_context: control_models.ControlContext,
    terminal_driver: TerminalDriver,
    window_id: WindowId,
) -> control_models.ControlResult:
    native_state, reason = deliver_native_text(
        control_context,
        terminal_driver,
        window_id,
        request.discussion or "",
    )
    if native_state is None:
        return control_models.ControlResult(
            request.request_id,
            control_models.ControlAcknowledgement.INDETERMINATE,
            reason,
        )
    return control_models.ControlResult(request.request_id, control_models.ControlAcknowledgement.ACKNOWLEDGED)
