# Copyright (c) 2026 Zhambyl Yermagambet
"""The five audit writes, as an object over an injected repository.

This is what a daemon-side caller holds. `audit/record.py` beside it is the
same five writes as free functions over a repository nobody injected — the floor
that a free function deep in the tree, or a process with no graph at all, still
needs. The two spell the same rows because the floor delegates to this class.

The split is the point: anything that already takes its collaborators by
constructor takes this too, so "what did the machinery do" is a dependency you
can see in a signature and substitute in a test, not an import.
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path
from typing import TYPE_CHECKING

from audit import documents, failures, records
from domain.ids import ActorId, SessionId, TaskId
from repository.mapper import audit as mapper
from terminal.panes.contracts import PaneAudit

if TYPE_CHECKING:
    from repository.contract.audit import AuditWriteRepository


def script_name() -> str:
    """Return the current script file name.

    Returns:
        Current script file name.

    """
    return Path(sys.argv[0] or "python").name


class AuditRecorder(failures.ErrorRecorder, PaneAudit):
    """Write operational actions to an injected repository."""

    def __init__(self, audit_write_repository: AuditWriteRepository) -> None:
        """Create a recorder that uses the specified repository."""
        self.audit_write_repository = audit_write_repository

    def error(
        self,
        session_or_log: str = "",
        func: str = "",
        context: documents.AuditContent = None,
    ) -> None:
        """Record the active exception."""
        self.error_from(sys.exception(), session_or_log, func, context)

    def error_from(
        self,
        failure: BaseException | None,
        session_or_log: str = "",
        func: str = "",
        context: documents.AuditContent = None,
    ) -> None:
        """Record an exception that the caller can hold outside its `except` block.

        A web framework exception handler receives its exception as an argument,
        and it can run on a thread where no exception is active.
        """
        self.audit_write_repository.record_error(
            records.ApplicationErrorRecord(
                session_id=SessionId(session_or_log),
                script=script_name(),
                function=func,
                traceback="".join(traceback.format_exception(failure)),
                context="" if context is None else mapper.text(context),
                process_id=os.getpid(),
                timestamp=time.time(),
            ),
        )

    def state_file(
        self,
        log: str,
        path: str,
        action: str,
        content: documents.AuditContent = "",
    ) -> None:
        """Record one state-file operation."""
        self.audit_write_repository.record_state_file(
            records.StateFileRecord(
                session_id=SessionId(log),
                path=path,
                action=action,
                content=mapper.truncated(content),
                script=script_name(),
                process_id=os.getpid(),
                timestamp=time.time(),
            ),
        )

    def spawn(self, log: str, child_pid: int, argv: list[str], purpose: str = "") -> None:
        """Record one child-process spawn."""
        serialized_arguments = mapper.text(
            records.SpawnArguments(tuple(str(argument) for argument in argv)),
        )
        self.audit_write_repository.record_spawn(
            records.SpawnRecord(
                session_id=SessionId(log),
                parent_script=script_name(),
                child_process_id=child_pid,
                argv=serialized_arguments,
                purpose=purpose,
                timestamp=time.time(),
            ),
        )

    def stream_start(
        self,
        log: str,
        kind: str,
        agent_id: ActorId | None = None,
        task_id: TaskId | None = None,
        src_path: str = "",
    ) -> records.StreamHandle | None:
        """Open an audited output stream.

        Returns:
            The stream handle.

        """
        return self.audit_write_repository.open_stream(
            records.StreamOpened(
                session_id=SessionId(log),
                kind=kind,
                agent_id=agent_id or ActorId(""),
                task_id=task_id or TaskId(""),
                source_path=src_path,
                process_id=os.getpid(),
                started_at=time.time(),
            ),
        )

    def stream_end(
        self,
        stream_handle: records.StreamHandle | None,
        end_reason: str,
        lines_emitted: int | None = None,
    ) -> None:
        """Close an audited output stream."""
        self.audit_write_repository.close_stream(stream_handle, end_reason, lines_emitted)
