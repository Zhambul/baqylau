"""Where the diagnostic database lives, how it opens, and what it holds.

Every writer in the tree opens this file, from short-lived hook processes to
the daemon, so the connection policy is WAL and the schema is applied on every
connect: no migration step, no initialization order to get wrong.
"""

from __future__ import annotations

import os
import sqlite3

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


def connect() -> sqlite3.Connection:
    os.makedirs(audit_dir(), exist_ok=True)
    connection = sqlite3.connect(db_path(), timeout=10)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.executescript(SCHEMA)
    return connection
