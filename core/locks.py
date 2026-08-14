# core/locks.py — pid-liveness locks over an ARBITRARY caller-supplied DB path
# (the harness per-repo claims DB, the OTLP receiver's per-machine singleton lock).
# Deliberately separate from core/state.py, whose charter is the per-SESSION
# state DB — these locks just reuse its connection/transaction machinery (the
# `claims` table is part of the shared schema) and its pid_alive probe.
# (Named lock_*, not claim — that name collided with core.slots.claim, a
# different mechanism. Was O_EXCL pid files: harness mirror-claims + watch lock.)

import os
import sqlite3
from contextlib import contextmanager

from core.process import process_is_alive


def _connect(database_path, create=True):
    if not create and not os.path.isfile(database_path):
        return None
    try:
        os.makedirs(os.path.dirname(database_path), exist_ok=True)
        connection = sqlite3.connect(database_path, timeout=5)
        connection.execute(
            "CREATE TABLE IF NOT EXISTS claims(key TEXT PRIMARY KEY, pid INTEGER NOT NULL)"
        )
        return connection
    except sqlite3.Error:
        return None


@contextmanager
def _transaction(connection):
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def lock_acquire(db, key, pid=None, create=True):
    """Take `key` for `pid` in the claims table at `db`.

    Returns ``claim``, ``steal-stale``, or a ``claim-denied`` result. A dead
    holder is replaced.

    create=False refuses to CREATE a missing DB file ('claim-denied:no-db'):
    for locks living inside the per-session STATE DB, whose file-existence is
    the session-alive signal — a slow-starting watcher whose session parked
    before its first write must NOT resurrect the DB (state.connect_existing's
    mode=rw open makes the refusal race-free against a concurrent park)."""
    pid = pid or os.getpid()
    conn = _connect(db, create=create)
    if conn is None:
        return "claim-denied:no-db"
    try:
        with _transaction(conn):
            row = conn.execute("SELECT pid FROM claims WHERE key=?", (key,)).fetchone()
            if row is None:
                conn.execute("INSERT INTO claims(key, pid) VALUES(?, ?)", (key, pid))
                return "claim"
            holder = int(row[0] or 0)
            if holder and holder != pid and process_is_alive(holder):
                return f"claim-denied:{holder}"
            conn.execute("UPDATE claims SET pid=? WHERE key=?", (pid, key))
            return "claim" if holder == pid else "steal-stale"
    except Exception:
        return "claim-denied:error"
    finally:
        conn.close()


def lock_holder(db, key):
    """The pid currently holding lock `key` in the DB at `db`, or 0. Read-only peek
    (unlike lock_acquire, which mutates) — used by the OTLP receiver to notice when
    its own singleton lock has been stolen out from under it."""
    conn = _connect(db)
    if conn is None:
        return 0
    try:
        row = conn.execute("SELECT pid FROM claims WHERE key=?", (key,)).fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    except Exception:
        return 0
    finally:
        conn.close()


def lock_release(db, key, pid=None):
    pid = pid or os.getpid()
    conn = _connect(db)
    if conn is None:
        return
    try:
        conn.execute("DELETE FROM claims WHERE key=? AND pid=?", (key, pid))
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()
