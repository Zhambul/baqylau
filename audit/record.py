"""Write operational audit without a graph — the floor, and only the floor.

Five free functions over a lazily-built repository, and the ONE place in the
application where a repository is reached without being injected. What is left
here after the daemon learned to inject `AuditRecorder` (audit/recorder.py)
is the set of writers that genuinely cannot take one:

  * `dashboard/cli.py`, which audits a spawn before the daemon it spawned exists;
  * free functions deep in the tree — the clipboard read, a notify channel's
    delivery failure — whose callers hold no graph and whose signatures would
    have to grow one purely to pass a recorder through.

Everything with a constructor takes `AuditRecorder` instead and does not come
through here. The rows are identical either way: these functions delegate.

Every CALLER is inside the daemon or one of its CLI verbs. It used to be called
from the `except` blocks of nine short-lived processes outside it — which is what
put `repository/impl/sqlite` in the failure path of every hook (measured:
+122 ms, and nine foreign writers of audit.db). Those processes are clients now
(`client/`): they import nothing of ours and record nothing at all, and what the
daemon can see about a delivery it refused is audited by the endpoint that
refused it.
"""

from __future__ import annotations

from threading import Lock

from pydantic import JsonValue

from audit.models import StreamHandle
from audit.recorder import AuditRecorder
from domain.ids import ActorId, TaskId
from repository.impl.sqlite.databases import audit_database
from repository.impl.sqlite.audit import (
    SqliteAuditWriteRepository,
    audit_enabled,
)

_recorders: dict[str, AuditRecorder] = {}
_recorders_lock = Lock()


def recorder() -> AuditRecorder:
    """The recorder for the audit file this process's environment names.

    Built on first use, never at import: a process that records nothing must not
    pay for opening a database. Cached BY PATH rather than as one singleton, so a
    process whose data directory changes gets the right file without a reset —
    and so `initialize()` still runs once per file. This cache is the reason the
    module is the floor and not a node: a floor has no owner to hang a scope on.
    """
    database = audit_database()
    with _recorders_lock:
        found = _recorders.get(database.path)
        if found is None:
            found = AuditRecorder(SqliteAuditWriteRepository(database))
            _recorders[database.path] = found
        return found


def enabled() -> bool:
    return audit_enabled()


def error(
    session_or_log: str = "",
    func: str = "",
    context: JsonValue = None,
) -> None:
    recorder().error(session_or_log, func, context)


def state_file(
    log: str,
    path: str,
    action: str,
    content: JsonValue = "",
) -> None:
    recorder().state_file(log, path, action, content)


def spawn(log: str, child_pid: int, argv: list[str], purpose: str = "") -> None:
    recorder().spawn(log, child_pid, argv, purpose)


def stream_start(
    log: str,
    kind: str,
    agent_id: ActorId | None = None,
    task_id: TaskId | None = None,
    src_path: str = "",
) -> StreamHandle | None:
    return recorder().stream_start(
        log, kind, agent_id or ActorId(""), task_id or TaskId(""), src_path
    )


def stream_end(
    stream_handle: StreamHandle | None,
    end_reason: str,
    lines_emitted: int | None = None,
) -> None:
    recorder().stream_end(stream_handle, end_reason, lines_emitted)
