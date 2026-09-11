# Copyright (c) 2026 Zhambyl Yermagambet
"""Translate Claude Code attention answers and plan decisions."""

from domain.attention import AttentionAnswer, AttentionChoice, AttentionPrompt
from domain.outcomes import PlanState
from harness.impl.claude_code import ids as claude_ids
from harness.impl.claude_code.canonical import records


def attention_answers(
    arguments: records.ToolArguments,
    response_answers: records.QuestionAnswers | None = None,
) -> tuple[AttentionAnswer, ...]:
    """Return canonical attention answers.

    Returns:
        The answers.

    """
    native_answers = arguments.answers if response_answers is None else response_answers
    if native_answers is None:
        return ()
    answers: list[AttentionAnswer] = []
    for question_index, question in enumerate(arguments.questions or ()):
        answer = _attention_answer(question_index, question, native_answers)
        if answer is not None:
            answers.append(answer)
    return tuple(answers)


def _attention_answer(
    question_index: int,
    question: records.Question,
    native_answers: records.QuestionAnswers,
) -> AttentionAnswer | None:
    prompt = str(question.question or "")
    native_answer = native_answers.root.get(prompt)
    if native_answer is None:
        return None
    if isinstance(native_answer, list):
        labels = tuple(str(answer_label) for answer_label in native_answer)
    elif question.multi_select:
        labels = _multi_select_labels(native_answer)
    else:
        labels = (str(native_answer),)
    question_identity = str(question_index if question.id is None else question.id)
    prompt_id = claude_ids.question_id_from_claude_code(claude_ids.ClaudeCodeQuestionId(question_identity))
    return AttentionAnswer(prompt_id=prompt_id, labels=labels)


def _multi_select_labels(native_answer: str) -> tuple[str, ...]:
    labels = [
        part.strip()
        for part in str(native_answer).split(", ")
        if part.strip()
    ]
    return tuple(labels)


def question_choices(question: records.Question) -> tuple[AttentionChoice, ...]:
    """Map the choices of a native question.

    Returns:
        Choices in their original order, with empty descriptions omitted.

    """
    choices = [
        AttentionChoice(option.label or "", option.description or None)
        for option in question.options or ()
    ]
    return tuple(choices)


def questions(arguments: records.ToolArguments) -> tuple[AttentionPrompt, ...]:
    """Map native questions to attention prompts.

    Returns:
        Prompts with stable identifiers and their available choices.

    """
    prompts = []
    for index, question in enumerate(arguments.questions or ()):
        question_identity = str(index if question.id is None else question.id)
        prompts.append(
            AttentionPrompt(
                prompt_id=claude_ids.question_id_from_claude_code(
                    claude_ids.ClaudeCodeQuestionId(question_identity),
                ),
                title=question.header or None,
                prompt=question.question or "",
                multiple=bool(question.multi_select),
                choices=question_choices(question),
            ),
        )
    return tuple(prompts)


def plan_resolution(
    tool_response: records.ToolResponse | records.ToolResponseBlocks | str | None,
    *,
    failed: bool,
) -> tuple[PlanState, str | None, bool]:
    """Return the plan resolution.

    Returns:
        The plan state, feedback, and edit flag.

    """
    if not failed:
        edited = bool(isinstance(tool_response, records.ToolResponse) and tool_response.plan_was_edited)
        return PlanState.APPROVED, None, edited
    if isinstance(tool_response, str):
        text = tool_response
    elif tool_response is None:
        text = "{}"
    else:
        text = tool_response.model_dump_json(exclude_none=True)
    marker = "the user said:"
    marker_position = text.find(marker)
    if marker_position >= 0:
        feedback = text[marker_position + len(marker) :].strip()
        return PlanState.CHANGES_REQUESTED, feedback, False
    return PlanState.REJECTED, None, False
