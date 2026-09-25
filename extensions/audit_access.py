# Copyright (c) 2026 Zhambyl Yermagambet
"""Record one worker's typed diagnostics in the host's operational audit."""

import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from baqylau_extension_api.contracts.reporting import ExtensionAuditService
from baqylau_extension_api.models import reporting
from baqylau_extension_api.models.scopes import SessionScope

from audit.records import ApplicationErrorRecord
from domain.ids import SessionId
from repository.contract.audit import AuditWriteRepository


@dataclass(frozen=True)
class HostAuditService(ExtensionAuditService):
    """Record a worker's diagnostics in the audit errors table, named by its package and code."""

    extension_id: str
    audit: AuditWriteRepository
    clock: Callable[[], float] = field(default=time.time)

    def record_diagnostic(self, diagnostic_record: reporting.DiagnosticRecord) -> reporting.DiagnosticRecorded:
        """Store one diagnostic with its context.

        Returns:
            The confirmation.

        """
        checked = reporting.DiagnosticRecord.model_validate(diagnostic_record)
        scope = checked.scope
        session = scope.session_id if isinstance(scope, SessionScope) else ""
        self.audit.record_error(ApplicationErrorRecord(
            session_id=SessionId(session), script=f"extension:{self.extension_id}",
            function=checked.diagnostic.code, traceback="", context=checked.model_dump_json(),
            process_id=os.getpid(), timestamp=self.clock(),
        ))
        return reporting.DiagnosticRecorded()
