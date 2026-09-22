# Copyright (c) 2026 Zhambyl Yermagambet
"""Publish core session and selection payloads."""

from typing import Literal

from baqylau_extension_api.core.base import CorePayloadModel
from baqylau_extension_api.core.references import AccountReference, ModelReference
from baqylau_extension_api.core.states import EffortChangeReason, ModelChangeReason, Outcome, TitleOrigin
from baqylau_extension_api.models.base import OpaqueId


class SessionStarted(CorePayloadModel):
    """Record session start or resume context."""

    kind: Literal["session.started"] = "session.started"
    working_directory: str
    source_reference: str
    resumed_from: OpaqueId | None
    title: str | None
    model: ModelReference | None
    effort: str | None
    account: AccountReference | None
    continued_from: OpaqueId | None = None


class SessionTitleChanged(CorePayloadModel):
    """Record a title and its source."""

    kind: Literal["session.title_changed"] = "session.title_changed"
    title: str
    origin: TitleOrigin


class SessionAccountChanged(CorePayloadModel):
    """Record a session's account."""

    kind: Literal["session.account_changed"] = "session.account_changed"
    account: AccountReference


class SessionFinished(CorePayloadModel):
    """Record a session's final outcome."""

    kind: Literal["session.finished"] = "session.finished"
    outcome: Outcome
    reason: str | None


class ModelChanged(CorePayloadModel):
    """Record a model change and its cause."""

    kind: Literal["model.changed"] = "model.changed"
    previous: ModelReference | None
    current: ModelReference
    reason: ModelChangeReason


class EffortChanged(CorePayloadModel):
    """Record an effort change and its cause."""

    kind: Literal["effort.changed"] = "effort.changed"
    previous: str | None
    current: str
    reason: EffortChangeReason
