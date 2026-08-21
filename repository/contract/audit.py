"""The operational database: what the MACHINERY did.

Split in two on purpose. The WRITE side is reached from every process in the
tree, including short-lived hook processes calling it from inside an `except`
block — so it never raises, and it is a no-op when the audit is switched off.
The READ side is the daemon's, and opens the file read-only.
"""

from __future__ import annotations

from typing import Mapping, Protocol

from audit.models import (
    ApplicationError,
    ApplicationErrorRecord,
    SpawnRecord,
    StateFileRecord,
    StreamHandle,
    StreamOpened,
)
from domain.ids import SessionId


class AuditWriteRepository(Protocol):
    """Every method swallows storage failures: a broken auditor must never take
    down the thing it exists to explain."""

    def record_error(self, application_error_record: ApplicationErrorRecord) -> None: ...

    def record_state_file(self, state_file_record: StateFileRecord) -> None: ...

    def record_spawn(self, spawn_record: SpawnRecord) -> None: ...

    def open_stream(self, stream_opened: StreamOpened) -> StreamHandle | None:
        """None when the audit is off, or when the row could not be written."""
        ...

    def close_stream(
        self,
        stream_handle: StreamHandle | None,
        end_reason: str,
        lines_emitted: int | None,
    ) -> None:
        """A None handle is an accepted no-op — the open may have been skipped."""
        ...


class AuditReadRepository(Protocol):
    def errors_for_session(self, session_id: SessionId) -> tuple[ApplicationError, ...]: ...

    def error_counts(self) -> Mapping[SessionId, int]:
        """Every session's error count in one query — the ⚠ warning light."""
        ...
