# Copyright (c) 2026 Zhambyl Yermagambet
"""Submit native question answers."""

from pydantic import TypeAdapter, ValidationError

from domain.event_work import QuestionAsked
from harness.contract import ControlHandler
from harness.impl.opencode2 import permission_answer, question_input, question_steps
from harness.impl.opencode2.question_discussion import discuss
from harness.impl.opencode2.question_records import AnswerDraft
from harness.models import controls

PERMISSION_PREFIX = "per_"


class AnswerQuestionHandler(ControlHandler):
    """Answer the question visible in the owned terminal."""

    def __call__(
        self, request: controls.ControlRequest, control_context: controls.ControlContext,
    ) -> controls.ControlResult:
        """Submit the selected answers or discussion text.

        Returns:
            The input result, or a rejection for an unsupported answer shape.

        """
        if not isinstance(request, controls.AnswerQuestion):
            return _rejected(request)
        pending = _pending_question(request, control_context)
        if pending is None:
            return _rejected(request)
        if str(pending.attention_id).startswith(PERMISSION_PREFIX):
            return permission_answer.submit(request, control_context, _answers(request))
        if request.decision == controls.AnswerDecision.DISCUSS:
            return discuss(request, control_context)
        return _submit_answer(request, control_context, pending)


def _submit_answer(
    request: controls.AnswerQuestion, context: controls.ControlContext, question_asked: QuestionAsked,
) -> controls.ControlResult:
    steps = question_steps.steps(question_asked, _answers(request))
    if steps is None:
        return _rejected(request)
    return question_input.submit(request, context, steps)


def _pending_question(
    answer_question: controls.AnswerQuestion, control_context: controls.ControlContext,
) -> QuestionAsked | None:
    pending = control_context.pending_attention
    if not isinstance(pending, QuestionAsked) or control_context.terminal_window_id is None:
        return None
    return pending if pending.attention_id == answer_question.attention_id else None


def _answers(answer_question: controls.AnswerQuestion) -> tuple[AnswerDraft, ...]:
    if answer_question.answers is None or answer_question.decision != controls.AnswerDecision.ANSWER:
        return ()
    try:
        return TypeAdapter(tuple[AnswerDraft, ...]).validate_json(answer_question.answers.json_text)
    except ValidationError:
        return ()


def _rejected(request: controls.ControlRequest) -> controls.ControlResult:
    return controls.ControlResult(
        request.request_id, controls.ControlAcknowledgement.REJECTED, "OpenCode2 question answer is not valid",
    )
