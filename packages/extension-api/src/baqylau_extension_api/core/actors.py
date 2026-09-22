# Copyright (c) 2026 Zhambyl Yermagambet
"""Publish core actor lifecycle and assignment payloads."""

from typing import Literal

from baqylau_extension_api.core.base import CorePayloadModel
from baqylau_extension_api.core.content import Content
from baqylau_extension_api.core.states import ActorRole, Outcome
from baqylau_extension_api.models.base import OpaqueId


class ActorStarted(CorePayloadModel):
    """Record the start of an actor."""

    kind: Literal["actor.started"] = "actor.started"
    name: str
    role: ActorRole


class ActorNameChanged(CorePayloadModel):
    """Record an actor's display name."""

    kind: Literal["actor.name_changed"] = "actor.name_changed"
    name: str


class ActorDescriptionChanged(CorePayloadModel):
    """Record an actor's description."""

    kind: Literal["actor.description_changed"] = "actor.description_changed"
    description: str


class ActorFinished(CorePayloadModel):
    """Record the end of an actor."""

    kind: Literal["actor.finished"] = "actor.finished"
    reason: str | None


class ActorAssignmentStarted(CorePayloadModel):
    """Record a child assignment and its source prompt."""

    kind: Literal["actor.assignment_started"] = "actor.assignment_started"
    assignment_id: OpaqueId
    brief: Content
    actor_name: str | None = None
    prompt: Content | None = None


class ActorAssignmentFinished(CorePayloadModel):
    """Record an assignment's final outcome."""

    kind: Literal["actor.assignment_finished"] = "actor.assignment_finished"
    assignment_id: OpaqueId
    outcome: Outcome
    result: Content | None
    reason: str | None
