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

from audit.models import (
    ApplicationErrorRecord,
    SpawnRecord,
    StateFileRecord,
    StreamHandle,
    StreamOpened,
)
from domain.ids import ActorId, SessionId, TaskId
from repository.contract.audit import AuditWriteRepository
from repository.mapper import audit as mapper


def script_name() -> str:
    return os.path.basename(sys.argv[0] or "python")


class AuditRecorder:
    """What the machinery did, written where the daemon can read it back."""

    def __init__(self, audit_write_repository: AuditWriteRepository) -> None:
        self.audit_write_repository = audit_write_repository

    def error(
        self,
        session_or_log: str = "",
        func: str = "",
        context: object = None,  # loose: audit payload, wave 2 gives it a real shape
    ) -> None:
        self.audit_write_repository.record_error(
            ApplicationErrorRecord(
                session_id=SessionId(session_or_log),
                script=script_name(),
                function=func,
                traceback=traceback.format_exc(),
                context=mapper.text(context) if context is not None else "",
                process_id=os.getpid(),
                timestamp=time.time(),
            )
        )

    def state_file(
        self,
        log: str,
        path: str,
        action: str,
        content: object = "",  # loose: audit payload, wave 2 gives it a real shape
    ) -> None:
        self.audit_write_repository.record_state_file(
            StateFileRecord(
                session_id=SessionId(log),
                path=path,
                action=action,
                content=mapper.truncated(content),
                script=script_name(),
                process_id=os.getpid(),
                timestamp=time.time(),
            )
        )

    def spawn(self, log: str, child_pid: int, argv: list[str], purpose: str = "") -> None:
        self.audit_write_repository.record_spawn(
            SpawnRecord(
                session_id=SessionId(log),
                parent_script=script_name(),
                child_process_id=child_pid,
                argv=mapper.text([str(argument) for argument in argv]),
                purpose=purpose,
                timestamp=time.time(),
            )
        )

    def stream_start(
        self,
        log: str,
        kind: str,
        agent_id: ActorId | None = None,
        task_id: TaskId | None = None,
        src_path: str = "",
    ) -> StreamHandle | None:
        return self.audit_write_repository.open_stream(
            StreamOpened(
                session_id=SessionId(log),
                kind=kind,
                agent_id=agent_id or ActorId(""),
                task_id=task_id or TaskId(""),
                source_path=src_path,
                process_id=os.getpid(),
                started_at=time.time(),
            )
        )

    def stream_end(
        self,
        stream_handle: StreamHandle | None,
        end_reason: str,
        lines_emitted: int | None = None,
    ) -> None:
        self.audit_write_repository.close_stream(stream_handle, end_reason, lines_emitted)
