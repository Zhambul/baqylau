"""Write non-domain operational diagnostics.

Raw harness observations, translations, canonical events, and provenance live
in ``events.db``. This module records only application mechanics that are not
harness facts.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
import sys
import time
import traceback

SCHEMA = """
CREATE TABLE IF NOT EXISTS errors(
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    session_id TEXT NOT NULL,
    script TEXT NOT NULL,
    func TEXT NOT NULL,
    traceback TEXT NOT NULL,
    context TEXT NOT NULL,
    pid INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS errors_by_session ON errors(session_id, ts);
CREATE TABLE IF NOT EXISTS state_files(
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    session_id TEXT NOT NULL,
    path TEXT NOT NULL,
    action TEXT NOT NULL,
    content TEXT NOT NULL,
    script TEXT NOT NULL,
    pid INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS spawns(
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    session_id TEXT NOT NULL,
    parent_script TEXT NOT NULL,
    child_pid INTEGER NOT NULL,
    argv TEXT NOT NULL,
    purpose TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS streams(
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    src_path TEXT NOT NULL,
    pid INTEGER NOT NULL,
    started_at REAL NOT NULL,
    ended_at REAL,
    end_reason TEXT,
    lines_emitted INTEGER
);
"""


def enabled() -> bool:
    return os.environ.get("BAQYLAU_AUDIT", "1") != "0"


def audit_dir() -> str:
    configured = (os.environ.get("BAQYLAU_AUDIT_DIRECTORY") or "").strip()
    return configured or os.path.expanduser("~/.local/share/baqylau/audit")


def db_path() -> str:
    return os.path.join(audit_dir(), "audit.db")


def _connect() -> sqlite3.Connection:
    os.makedirs(audit_dir(), exist_ok=True)
    connection = sqlite3.connect(db_path(), timeout=10)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.executescript(SCHEMA)
    return connection


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
    with closing(_connect()) as connection, connection:
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
    with closing(_connect()) as connection, connection:
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


def spawn(log: str, child_pid: int, argv: list, purpose: str = "") -> None:
    if not enabled():
        return
    with closing(_connect()) as connection, connection:
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
    with closing(_connect()) as connection, connection:
        cursor = connection.execute(
            "INSERT INTO streams(session_id, kind, agent_id, task_id, src_path, pid, started_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (_session_id(log), kind, agent_id, task_id, src_path, os.getpid(), time.time()),
        )
        return int(cursor.lastrowid)


def stream_end(stream_id: int | None, end_reason: str, lines_emitted: int | None = None) -> None:
    if stream_id is None or not enabled():
        return
    with closing(_connect()) as connection, connection:
        connection.execute(
            "UPDATE streams SET ended_at=?, end_reason=?, lines_emitted=? WHERE id=?",
            (time.time(), end_reason, lines_emitted, stream_id),
        )
