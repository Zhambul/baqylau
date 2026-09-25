# Copyright (c) 2026 Zhambyl Yermagambet
"""Record worker diagnostics in the audit, named by package, code, and session."""

from dataclasses import dataclass, field

from baqylau_extension_api.models.documents import Diagnostic
from baqylau_extension_api.models.reporting import DiagnosticRecord
from baqylau_extension_api.models.scopes import SessionScope

from audit.records import ApplicationErrorRecord
from extensions.audit_access import HostAuditService

SCOPE = SessionScope(session_id="session-one", actor_id="lead", harness="claude")
DIAGNOSTIC = Diagnostic(code="read_failed", message="No log.")


@dataclass
class RecordingAudit:
    """Keep the audit records."""

    errors: list[ApplicationErrorRecord] = field(default_factory=list)

    def record_error(self, application_error_record: ApplicationErrorRecord) -> None:
        """Keep one record."""
        self.errors.append(application_error_record)


def test_diagnostic_is_audited_with_its_session() -> None:
    """The audit row names the package, the code, and the session of a session scope."""
    audit = RecordingAudit()
    service = HostAuditService("test.owner", audit, clock=lambda: 1.0)  # type: ignore[arg-type]

    service.record_diagnostic(DiagnosticRecord(diagnostic=DIAGNOSTIC, scope=SCOPE))

    error = audit.errors[0]
    assert (error.session_id, error.script, error.function) == ("session-one", "extension:test.owner", "read_failed")
