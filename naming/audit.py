"""Declared audit documents for automatic naming."""

from audit.models import AuditDocument


class NamingAudit(AuditDocument):
    job_key: str
    status: str | None = None
    title: str | None = None
    error_type: str | None = None
    error: str | None = None
