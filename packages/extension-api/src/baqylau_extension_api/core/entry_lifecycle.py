# Copyright (c) 2026 Zhambyl Yermagambet
"""Publish typed core compaction, assignment, and selection feed bodies."""

from typing import Literal

from baqylau_extension_api.core.content import Content
from baqylau_extension_api.core.derived_states import RunState
from baqylau_extension_api.core.entry_base import CoreEntryBodyModel
from baqylau_extension_api.models.base import OpaqueId


class CompactionStartedBody(CoreEntryBodyModel):
    """Keep optional token usage before compaction."""

    kind: Literal["compaction_started"] = "compaction_started"
    before_tokens: int | None = None


class CompactionFinishedBody(CoreEntryBodyModel):
    """Keep token usage and optional retained context after compaction."""

    kind: Literal["compaction_finished"] = "compaction_finished"
    before_tokens: int | None = None
    after_tokens: int | None = None
    context: Content | None = None


class AssignmentStartedBody(CoreEntryBodyModel):
    """Keep the assigned actor name and optional prompt."""

    kind: Literal["assignment_started"] = "assignment_started"
    assignment_id: OpaqueId
    assigned_actor_name: str | None = None
    prompt: Content | None = None


class AssignmentFinishedBody(CoreEntryBodyModel):
    """Keep the final assignment state and optional result."""

    kind: Literal["assignment_finished"] = "assignment_finished"
    assignment_id: OpaqueId
    state: RunState = "succeeded"
    result: Content | None = None


class ModelChangeBody(CoreEntryBodyModel):
    """Keep the visible model selection and fallback flag."""

    kind: Literal["model_change"] = "model_change"
    current: str
    previous: str | None = None
    automatic: bool = False


class EffortChangeBody(CoreEntryBodyModel):
    """Keep the visible effort selection."""

    kind: Literal["effort_change"] = "effort_change"
    current: str
    previous: str | None = None
