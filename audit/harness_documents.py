# Copyright (c) 2026 Zhambyl Yermagambet
"""Audit documents for harness input and startup operations."""

from audit.documents import AuditDocument
from domain.ids import HarnessName, WindowId


class HarnessErrorAudit(AuditDocument):
    """Describe one harness processing error."""

    harness: HarnessName
    error: str
    kind: str | None = None
    payload_bytes: int | None = None


class HarnessStartupAudit(AuditDocument):
    """Describe the observed result of a harness startup."""

    harness: HarnessName
    window_id: WindowId
    screen_kind: str | None = None
    outcome: str
    message: str
    screen: str | None = None
