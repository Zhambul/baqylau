"""Closed audit documents for notification work."""

from __future__ import annotations

from audit.models import AuditDocument
from domain.ids import SessionId


class NotificationSessionAudit(AuditDocument):
    session_id: SessionId | None


class NotificationSuppressedAudit(AuditDocument):
    session_id: SessionId
    kind: str
    reason: str


class NotificationRouteCandidateAudit(AuditDocument):
    device: str
    label: str | None
    age_s: float | None


class NotificationRouteAudit(AuditDocument):
    target: str | None
    target_label: str | None
    subscription_count: int
    candidates: tuple[NotificationRouteCandidateAudit, ...]
    session_id: SessionId
    kind: str


class NotificationRetractionAudit(AuditDocument):
    session_id: SessionId
    channel: str
    kind: str | None
    reason: str
    outcome: str
    age_seconds: float


class WebPushAudit(AuditDocument):
    session_id: SessionId
    kind: str | None
    action: str
    status: int
    ok: bool
    gone: bool
    error: str
    badge: int
    device: str | None
    endpoint: str


class TelegramSendAudit(AuditDocument):
    session_id: SessionId | None
    kind: str | None
    reason: str | None
    ok: bool
    status: int
    error: str
    retractable: bool
    message_id: int | None


class TelegramRetractionAudit(AuditDocument):
    session_id: SessionId | None
    kind: str | None
    message_id: int | None
    outcome: str
    status: int
    error: str
