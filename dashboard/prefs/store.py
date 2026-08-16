# dashboard/prefs/store.py — the kv table itself: open it, read one key, write one key.
#
# A tiny durable table (key TEXT PRIMARY KEY, val JSON) at
# paths.DASHBOARD_PREFERENCES_DATABASE, holding what YOU chose rather than what
# a session did. The modules beside this one each own one key and give it a
# typed shape; nothing outside them touches a key by name.
#
# It CREATES its database on demand (mode=rwc). A per-session state DB must
# never be created by a reader, because its existence is the session-alive
# signal — a global preferences DB carries no such meaning, so a first-ever
# write just makes it.
#
# Every call opens a fresh short-lived connection: the daemon serves requests on
# many threads and sqlite connections are thread-bound. One current schema, and
# clear failures on connection, decoding, and write errors.
import json
import os
import sqlite3

from dashboard import paths


def _connect():
    """A fresh writable connection to the durable preferences database. WAL keeps
    reads from blocking concurrent writes from other request threads."""
    path = paths.DASHBOARD_PREFERENCES_DATABASE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    connection = sqlite3.connect(path, timeout=5.0)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, val TEXT)")
    return connection


def _upsert(connection, key, value):
    """Write one JSON value through the store's single upsert statement."""
    connection.execute(
        "INSERT INTO kv(key, val) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET val = excluded.val",
        (key, json.dumps(value, ensure_ascii=False)),
    )


def get(key, default=None):
    """Return the decoded current-schema value, or `default` when absent."""
    connection = _connect()
    try:
        row = connection.execute("SELECT val FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default
    finally:
        connection.close()


def set(key, value):
    """Upsert `value` and return only after the transaction commits."""
    connection = _connect()
    try:
        _upsert(connection, key, value)
        connection.commit()
        return True
    finally:
        connection.close()


def mutate_map(key, mutator):
    """Atomically read-modify-write the DICT stored under `key`: load it (or a
    fresh object), apply `mutator` in place, and persist inside one
    BEGIN IMMEDIATE transaction. The get()+set() pattern its callers used spans
    TWO short-lived connections, so two concurrent control-plane POSTs (each its
    own request thread + connection) could both read the old map and the second
    write clobber the first. BEGIN IMMEDIATE takes the write lock before the
    read, so a racing mutation observes the committed map. Returns the committed
    map and raises if any part of the transaction fails."""
    connection = _connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT val FROM kv WHERE key=?", (key,)).fetchone()
        document = json.loads(row[0]) if row else {}
        if not isinstance(document, dict):
            raise TypeError(f"preference {key!r} must contain an object")
        mutator(document)
        _upsert(connection, key, document)
        connection.commit()
        return document
    finally:
        connection.close()


def stored_object(key):
    document = get(key, {})
    if not isinstance(document, dict):
        raise TypeError(f"preference {key!r} must contain an object")
    return document
