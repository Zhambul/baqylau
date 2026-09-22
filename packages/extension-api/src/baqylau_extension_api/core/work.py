# Copyright (c) 2026 Zhambyl Yermagambet
"""Publish core task, goal, question, and plan payloads."""

from typing import Literal

from baqylau_extension_api.core.attention import AttentionAnswer, AttentionPrompt
from baqylau_extension_api.core.base import CorePayloadModel
from baqylau_extension_api.core.content import Content
from baqylau_extension_api.core.states import GoalState, PlanState, TaskState
from baqylau_extension_api.models.base import OpaqueId


class TaskChanged(CorePayloadModel):
    """Record a task and its current owner."""

    kind: Literal["task.changed"] = "task.changed"
    task_id: OpaqueId
    subject: str
    description: str | None
    state: TaskState
    owner_actor_id: OpaqueId | None


class TaskListChanged(CorePayloadModel):
    """Record ordered task list membership."""

    kind: Literal["task.list_changed"] = "task.list_changed"
    list_id: OpaqueId
    task_ids: tuple[OpaqueId, ...]


class GoalChanged(CorePayloadModel):
    """Record a session goal's state."""

    kind: Literal["goal.changed"] = "goal.changed"
    objective: str | None
    state: GoalState
    reason: str | None


class QuestionAsked(CorePayloadModel):
    """Record questions that need a person's answer."""

    kind: Literal["question.asked"] = "question.asked"
    attention_id: OpaqueId
    questions: tuple[AttentionPrompt, ...]


class QuestionAnswered(CorePayloadModel):
    """Record answers to a question request."""

    kind: Literal["question.answered"] = "question.answered"
    attention_id: OpaqueId
    answers: tuple[AttentionAnswer, ...]
    feedback: str | None


class PlanProposed(CorePayloadModel):
    """Record a plan that needs a person's decision."""

    kind: Literal["plan.proposed"] = "plan.proposed"
    attention_id: OpaqueId
    plan: Content


class PlanResolved(CorePayloadModel):
    """Record a person's decision on a plan."""

    kind: Literal["plan.resolved"] = "plan.resolved"
    attention_id: OpaqueId
    state: PlanState
    feedback: str | None
    edited: bool
