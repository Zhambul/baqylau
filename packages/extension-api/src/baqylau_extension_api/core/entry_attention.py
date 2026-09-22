# Copyright (c) 2026 Zhambyl Yermagambet
"""Publish typed core skill, question, and plan feed bodies."""

from typing import Literal

from baqylau_extension_api.core.attention import AttentionAnswer, AttentionPrompt
from baqylau_extension_api.core.content import Content
from baqylau_extension_api.core.derived_states import RunState
from baqylau_extension_api.core.entry_base import CoreEntryBodyModel
from baqylau_extension_api.core.states import PlanState
from baqylau_extension_api.models.base import OpaqueId


class SkillStartedBody(CoreEntryBodyModel):
    """Keep the skill name, identity, and optional arguments."""

    kind: Literal["skill_started"] = "skill_started"
    skill_id: OpaqueId
    name: str
    arguments: Content | None = None


class SkillFinishedBody(CoreEntryBodyModel):
    """Keep the final skill state and optional result."""

    kind: Literal["skill_finished"] = "skill_finished"
    skill_id: OpaqueId
    state: RunState
    result: Content | None = None


class QuestionAskedBody(CoreEntryBodyModel):
    """Keep the full typed questions and their attention identity."""

    kind: Literal["question_asked"] = "question_asked"
    attention_id: OpaqueId
    questions: tuple[AttentionPrompt, ...]


class QuestionAnsweredBody(CoreEntryBodyModel):
    """Keep answers and optional feedback for one attention request."""

    kind: Literal["question_answered"] = "question_answered"
    attention_id: OpaqueId
    answers: tuple[AttentionAnswer, ...] = ()
    feedback: str | None = None


class PlanProposedBody(CoreEntryBodyModel):
    """Keep the proposed plan and its attention identity."""

    kind: Literal["plan_proposed"] = "plan_proposed"
    attention_id: OpaqueId
    plan: Content


class PlanResolvedBody(CoreEntryBodyModel):
    """Keep the plan decision, feedback, and edited state."""

    kind: Literal["plan_resolved"] = "plan_resolved"
    attention_id: OpaqueId
    state: PlanState
    feedback: str | None = None
    edited: bool = False
