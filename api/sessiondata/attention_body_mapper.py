# Copyright (c) 2026 Zhambyl Yermagambet
"""Map attention entry bodies to API responses."""

from __future__ import annotations

from typing import TYPE_CHECKING

from api.common.mapper import values
from api.sessiondata.models import entry as entry_models
from domain import entry_attention as attention_bodies

if TYPE_CHECKING:
    from domain import entry_base


def map_body(entry_body: entry_base.EntryBody) -> entry_models.EntryBodyResponse | None:
    """Return the API response for an attention entry body.

    Returns:
        The API response for an attention entry body.

    """
    if isinstance(entry_body, attention_bodies.SkillStartedBody):
        return entry_models.SkillStartedBodyResponse(
            skill_id=str(entry_body.skill_id),
            name=entry_body.name,
            arguments=values.maybe_content(entry_body.arguments),
        )
    if isinstance(entry_body, attention_bodies.SkillFinishedBody):
        return entry_models.SkillFinishedBodyResponse(
            skill_id=str(entry_body.skill_id), state=entry_body.state, result=values.maybe_content(entry_body.result),
        )
    if isinstance(entry_body, attention_bodies.QuestionAskedBody):
        return entry_models.QuestionAskedBodyResponse(
            attention_id=str(entry_body.attention_id),
            questions=tuple(
                entry_models.QuestionResponse(
                    question_id=question.prompt_id,
                    title=question.title,
                    question=question.prompt,
                    multiple=question.multiple,
                    choices=tuple(
                        entry_models.QuestionChoiceResponse(label=choice.label, description=choice.description)
                        for choice in question.choices
                    ),
                    free_text=question.free_text,
                )
                for question in entry_body.questions
            ),
        )
    if isinstance(entry_body, attention_bodies.QuestionAnsweredBody):
        return entry_models.QuestionAnsweredBodyResponse(
            attention_id=str(entry_body.attention_id),
            answers=tuple(
                entry_models.QuestionAnswerResponse(question_id=answer.prompt_id, labels=answer.labels)
                for answer in entry_body.answers
            ),
            feedback=entry_body.feedback,
        )
    if isinstance(entry_body, attention_bodies.PlanProposedBody):
        response: entry_models.EntryBodyResponse | None = entry_models.PlanProposedBodyResponse(
            attention_id=str(entry_body.attention_id), plan=values.content(entry_body.plan),
        )
    elif isinstance(entry_body, attention_bodies.PlanResolvedBody):
        response = entry_models.PlanResolvedBodyResponse(
            attention_id=str(entry_body.attention_id),
            state=entry_body.state,
            feedback=entry_body.feedback,
            edited=entry_body.edited,
        )
    else:
        response = None
    return response
