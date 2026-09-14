# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate question answers before sending any input."""

from dataclasses import dataclass

from domain.attention import AttentionPrompt
from domain.event_work import QuestionAsked
from harness.impl.opencode2.question_records import AnswerDraft


@dataclass(frozen=True)
class AnswerStep:
    """Select an option or the native custom-answer field."""

    keys: tuple[str, ...]
    other: str | None
    review: bool = False


def steps(
    question_asked: QuestionAsked, answers: tuple[AnswerDraft, ...],
) -> tuple[AnswerStep, ...] | None:
    """Prepare all answers before changing the dialog.

    Returns:
        Ordered steps, or None for an invalid answer.

    """
    if not answers or len(answers) != len(question_asked.questions):
        return None
    prepared = []
    for question, answer in zip(question_asked.questions, answers, strict=True):
        step = _step(question, answer)
        if step is None:
            return None
        prepared.append(step)
    return tuple(prepared)


def _step(attention_prompt: AttentionPrompt, answer_draft: AnswerDraft) -> AnswerStep | None:
    if attention_prompt.multiple:
        return _multiple(attention_prompt, answer_draft)
    index = _choice(attention_prompt, answer_draft)
    if index is None:
        return None
    down = tuple("down" for _ in range(index))
    return AnswerStep((*down, "enter"), answer_draft.other)


def _choice(attention_prompt: AttentionPrompt, answer_draft: AnswerDraft) -> int | None:
    if not answer_draft.selected and answer_draft.other:
        return len(attention_prompt.choices)
    if len(answer_draft.selected) != 1 or answer_draft.other:
        return None
    choices = tuple(choice.label for choice in attention_prompt.choices)
    selected = answer_draft.selected[0]
    return choices.index(selected) if selected in choices else None


def _multiple(attention_prompt: AttentionPrompt, answer_draft: AnswerDraft) -> AnswerStep | None:
    selected = answer_draft.selected
    if not selected or answer_draft.other:
        return None
    choices = tuple(choice.label for choice in attention_prompt.choices)
    if len(set(selected)) != len(selected):
        return None
    if any(label not in choices for label in selected):
        return None
    indices = sorted(choices.index(label) for label in selected)
    return AnswerStep(_toggle_keys(indices), None, review=True)


def _toggle_keys(indices: list[int]) -> tuple[str, ...]:
    keys: list[str] = []
    previous = 0
    for index in indices:
        keys.extend("down" for _ in range(index - previous))
        keys.append("enter")
        previous = index
    return (*keys, "tab")
