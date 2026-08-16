"""Write non-domain operational diagnostics — the one write API in the tree.

Raw harness observations, translations, canonical events, and provenance live
in ``events.db``. This module records only application mechanics that are not
harness facts.
"""

from __future__ import annotations

import json
import os
from contextlib import closing
import sys
import time
import traceback

from diagnostics.database import connect, enabled


def _script() -> str:
    return os.path.basename(sys.argv[0] or "python")


def _session_id(session_or_log: str) -> str:
    return session_or_log


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def error(session_or_log: str = "", func: str = "", context: object = None) -> None:
    if not enabled():
        return
    with closing(connect()) as connection, connection:
        connection.execute(
            "INSERT INTO errors(ts, session_id, script, func, traceback, context, pid) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                time.time(),
                _session_id(session_or_log),
                _script(),
                func,
                traceback.format_exc(),
                _text(context) if context is not None else "",
                os.getpid(),
            ),
        )


def state_file(log: str, path: str, action: str, content: object = "") -> None:
    if not enabled():
        return
    with closing(connect()) as connection, connection:
        connection.execute(
            "INSERT INTO state_files(ts, session_id, path, action, content, script, pid) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                time.time(),
                _session_id(log),
                path,
                action,
                _text(content)[:2000],
                _script(),
                os.getpid(),
            ),
        )


def spawn(log: str, child_pid: int, argv: list[str], purpose: str = "") -> None:
    if not enabled():
        return
    with closing(connect()) as connection, connection:
        connection.execute(
            "INSERT INTO spawns(ts, session_id, parent_script, child_pid, argv, purpose) "
            "VALUES(?,?,?,?,?,?)",
            (
                time.time(),
                _session_id(log),
                _script(),
                child_pid,
                _text([str(argument) for argument in argv]),
                purpose,
            ),
        )


def stream_start(
    log: str,
    kind: str,
    agent_id: str = "",
    task_id: str = "",
    src_path: str = "",
) -> int | None:
    if not enabled():
        return None
    with closing(connect()) as connection, connection:
        cursor = connection.execute(
            "INSERT INTO streams(session_id, kind, agent_id, task_id, src_path, pid, started_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (_session_id(log), kind, agent_id, task_id, src_path, os.getpid(), time.time()),
        )
        # lastrowid is Optional in the DB-API: it is set after this INSERT, but
        # int() on the None branch would raise TypeError rather than degrade,
        # and this function is already declared to return None when the audit
        # is off. Hand the value back as-is.
        return cursor.lastrowid


def stream_end(stream_id: int | None, end_reason: str, lines_emitted: int | None = None) -> None:
    if stream_id is None or not enabled():
        return
    with closing(connect()) as connection, connection:
        connection.execute(
            "UPDATE streams SET ended_at=?, end_reason=?, lines_emitted=? WHERE id=?",
            (time.time(), end_reason, lines_emitted, stream_id),
        )
