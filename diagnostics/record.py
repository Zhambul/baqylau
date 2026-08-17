"""Write operational diagnostics — the one write API in the tree.

Five free functions over a lazily-built repository, and the ONE place in the
application where a repository is reached without being injected: the daemon's
own boot and shutdown paths record before and after the graph that would inject
it exists, and `api/guard.py` records a rejection that happens before any
handler runs.

Every CALLER is inside the daemon. It used to be called from the `except` blocks
of nine short-lived processes outside it — which is what put
`repository/impl/sqlite` in the failure path of every hook (measured: +122 ms,
and nine foreign writers of audit.db). Those processes are clients now
(`client/`): they import nothing of ours and record nothing at all, and what the
daemon can see about a delivery it refused is audited by the endpoint that
refused it.

Daemon-side callers with a graph take `DiagnosticWriteRepository` by injection
and do not come through here.
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from threading import Lock

from diagnostics.models import (
    ApplicationErrorRecord,
    SpawnRecord,
    StateFileRecord,
    StreamHandle,
    StreamOpened,
)
from repository.contract.diagnostics import DiagnosticWriteRepository
from repository.impl.sqlite.databases import audit_database
from repository.impl.sqlite.diagnostics import (
    SqliteDiagnosticWriteRepository,
    audit_enabled,
)
from repository.mapper import diagnostics as mapper

_writers: dict[str, DiagnosticWriteRepository] = {}
_writers_lock = Lock()


def repository() -> DiagnosticWriteRepository:
    """The writer for the audit file this process's environment names.

    Built on first use, never at import: a hook process that records nothing
    must not pay for opening a database. Cached BY PATH rather than as one
    singleton, so a process whose data directory changes gets the right file
    without a reset — and so `initialize()` still runs once per file.
    """
    database = audit_database()
    with _writers_lock:
        writer = _writers.get(database.path)
        if writer is None:
            writer = SqliteDiagnosticWriteRepository(database)
            _writers[database.path] = writer
        return writer


def enabled() -> bool:
    return audit_enabled()


def _script() -> str:
    return os.path.basename(sys.argv[0] or "python")


def error(session_or_log: str = "", func: str = "", context: object = None) -> None:
    repository().record_error(
        ApplicationErrorRecord(
            session_id=session_or_log,
            script=_script(),
            function=func,
            traceback=traceback.format_exc(),
            context=mapper.text(context) if context is not None else "",
            process_id=os.getpid(),
            timestamp=time.time(),
        )
    )


def state_file(log: str, path: str, action: str, content: object = "") -> None:
    repository().record_state_file(
        StateFileRecord(
            session_id=log,
            path=path,
            action=action,
            content=mapper.truncated(content),
            script=_script(),
            process_id=os.getpid(),
            timestamp=time.time(),
        )
    )


def spawn(log: str, child_pid: int, argv: list[str], purpose: str = "") -> None:
    repository().record_spawn(
        SpawnRecord(
            session_id=log,
            parent_script=_script(),
            child_process_id=child_pid,
            argv=mapper.text([str(argument) for argument in argv]),
            purpose=purpose,
            timestamp=time.time(),
        )
    )


def stream_start(
    log: str,
    kind: str,
    agent_id: str = "",
    task_id: str = "",
    src_path: str = "",
) -> StreamHandle | None:
    return repository().open_stream(
        StreamOpened(
            session_id=log,
            kind=kind,
            agent_id=agent_id,
            task_id=task_id,
            source_path=src_path,
            process_id=os.getpid(),
            started_at=time.time(),
        )
    )


def stream_end(
    handle: StreamHandle | None,
    end_reason: str,
    lines_emitted: int | None = None,
) -> None:
    repository().close_stream(handle, end_reason, lines_emitted)
