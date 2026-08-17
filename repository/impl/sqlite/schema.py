"""Every table we own, in one file, with one version per database.

Two databases, and the reason each is its own file is in `core/data.py`.

The write split inside `main.db` is the design: `sessions` is written only by
the interpreter's session-upsert reaction (birth and upkeep derive from
committed facts), `raw_events` by any recorder, and the interpretation tables
only by the interpreter. Positions are not stored anywhere separate: a pulled
source resumes from the `source_position` of the last raw event carrying its
`source_identity`, so recorded progress can never drift from the evidence.

There is no key–value table. Nine preference entities that used to be JSON
blobs under nine keys have nine tables with real primary keys; the queue, the
dialog answers and the usage windows are rows rather than encoded lists. Three
opaque columns remain and each is deliberate: `canonical_events.payload` is the
canonical fact body, closed and versioned by `domain/codec.py`;
`raw_events.payload` is the verbatim bytes we observed, which is the whole point
of keeping it; `state_files.content` is a free-form audit blob written by a
facade whose contract is "record anything, never raise".
"""

from __future__ import annotations

MAIN_SCHEMA_VERSION = 2
AUDIT_SCHEMA_VERSION = 1

MAIN_MIGRATIONS: dict[int, tuple[str, ...]] = {
    2: (
        "ALTER TABLE translation_records RENAME TO interpretations",
        "ALTER TABLE canonical_provenance RENAME TO interpretation_events",
    ),
}


_SCHEMA_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_version(
    id INTEGER PRIMARY KEY CHECK(id = 1),
    version INTEGER NOT NULL,
    applied_at REAL NOT NULL
);
"""


MAIN_SCHEMA = _SCHEMA_VERSION_TABLE + """
-- === evidence and canonical facts =========================================

CREATE TABLE IF NOT EXISTS sessions(
    session_id TEXT PRIMARY KEY,
    lead_actor_id TEXT NOT NULL,
    harness TEXT NOT NULL,
    harness_session_id TEXT NOT NULL,
    source_reference TEXT NOT NULL,
    working_directory TEXT,
    terminal_window_id TEXT,
    harness_process_id INTEGER,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_events(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_event_id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    harness TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_identity TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_position TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    parent_actor_id TEXT,
    observed_at REAL NOT NULL,
    encoding TEXT NOT NULL,
    payload BLOB NOT NULL,
    terminal_window_id TEXT,
    harness_process_id INTEGER,
    account_id TEXT,
    account_display_name TEXT
);

CREATE INDEX IF NOT EXISTS index_raw_by_source
    ON raw_events(source_identity, id);

CREATE INDEX IF NOT EXISTS index_raw_by_session
    ON raw_events(session_id, observed_at);

CREATE TABLE IF NOT EXISTS interpretations(
    raw_event_id TEXT PRIMARY KEY,
    translator_version TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(
        decision IN ('translated', 'ignored_unknown', 'ignored_nonsemantic', 'translation_failed')
    ),
    reason TEXT,
    completed_at REAL NOT NULL,
    FOREIGN KEY(raw_event_id) REFERENCES raw_events(raw_event_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS canonical_events(
    cursor INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    schema_version INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    session_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    turn_id TEXT,
    parent_actor_id TEXT,
    harness TEXT NOT NULL,
    occurred_at REAL,
    terminal_window_id TEXT,
    harness_process_id INTEGER,
    accepted_at REAL NOT NULL,
    payload TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS index_canonical_session_type
    ON canonical_events(session_id, event_type, cursor);

CREATE INDEX IF NOT EXISTS index_canonical_session_actor
    ON canonical_events(session_id, actor_id, cursor);

CREATE INDEX IF NOT EXISTS index_canonical_session_cursor
    ON canonical_events(session_id, cursor);

CREATE TABLE IF NOT EXISTS interpretation_events(
    event_id TEXT NOT NULL,
    raw_event_id TEXT NOT NULL,
    event_order INTEGER NOT NULL,
    storage_result TEXT NOT NULL CHECK(storage_result IN ('accepted', 'deduplicated')),
    PRIMARY KEY(event_id, raw_event_id),
    UNIQUE(raw_event_id, event_order),
    FOREIGN KEY(event_id) REFERENCES canonical_events(event_id) ON DELETE CASCADE,
    FOREIGN KEY(raw_event_id) REFERENCES raw_events(raw_event_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS operation_output(
    session_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    harness TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    parent_actor_id TEXT,
    source_path TEXT NOT NULL,
    chunk_source_type TEXT NOT NULL,
    delete_source INTEGER NOT NULL,
    initial_size INTEGER NOT NULL,
    initial_modified_at INTEGER NOT NULL,
    wait_for_source_change INTEGER NOT NULL,
    until TEXT NOT NULL CHECK(until IN ('operation_finished', 'session_finished')),
    state TEXT NOT NULL CHECK(state IN ('active', 'finishing')),
    created_at REAL NOT NULL,
    PRIMARY KEY(session_id, operation_id)
);

-- === your unsent work on one session ======================================

CREATE TABLE IF NOT EXISTS session_workspaces(
    session_id TEXT PRIMARY KEY,
    composer_text TEXT NOT NULL DEFAULT '',
    composer_origin TEXT NOT NULL DEFAULT '',
    composer_sequence REAL NOT NULL DEFAULT 0,
    queue_origin TEXT NOT NULL DEFAULT '',
    dialog_attention_id TEXT,
    dialog_origin TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS composer_queue_items(
    session_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY(session_id, position),
    FOREIGN KEY(session_id) REFERENCES session_workspaces(session_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS dialog_answers(
    session_id TEXT NOT NULL,
    prompt_index INTEGER NOT NULL,
    other_text TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(session_id, prompt_index),
    FOREIGN KEY(session_id) REFERENCES session_workspaces(session_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS dialog_answer_selections(
    session_id TEXT NOT NULL,
    prompt_index INTEGER NOT NULL,
    selection_index INTEGER NOT NULL,
    selected_value TEXT NOT NULL,
    PRIMARY KEY(session_id, prompt_index, selection_index),
    FOREIGN KEY(session_id) REFERENCES session_workspaces(session_id) ON DELETE CASCADE
);

-- === what you chose =======================================================

CREATE TABLE IF NOT EXISTS notification_settings(
    id INTEGER PRIMARY KEY CHECK(id = 1),
    alerting_enabled INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS session_notification_mutes(
    session_id TEXT PRIMARY KEY,
    muted_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS session_view_modes(
    session_id TEXT PRIMARY KEY,
    view_mode TEXT NOT NULL CHECK(view_mode IN ('verbose', 'default', 'focus'))
);

CREATE TABLE IF NOT EXISTS hidden_directories(
    working_directory TEXT PRIMARY KEY,
    hidden_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS new_session_preferences(
    id INTEGER PRIMARY KEY CHECK(id = 1),
    working_directory TEXT,
    harness TEXT,
    model TEXT,
    effort TEXT
);

CREATE TABLE IF NOT EXISTS new_session_drafts(
    working_directory TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    sequence REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS task_dismissals(
    session_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    dismissed_at REAL NOT NULL,
    PRIMARY KEY(session_id, task_id)
);

CREATE TABLE IF NOT EXISTS push_subscriptions(
    endpoint TEXT PRIMARY KEY,
    public_key TEXT NOT NULL,
    authentication_secret TEXT NOT NULL,
    device_id TEXT NOT NULL,
    device_label TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS push_signing_keys(
    id INTEGER PRIMARY KEY CHECK(id = 1),
    private_key_pem TEXT NOT NULL,
    public_key TEXT NOT NULL
);

-- === the terminal's own state =============================================

CREATE TABLE IF NOT EXISTS pane_widths(
    working_directory TEXT PRIMARY KEY,
    width_percent INTEGER NOT NULL CHECK(width_percent BETWEEN 1 AND 99)
);

CREATE TABLE IF NOT EXISTS opened_views(
    content_reference TEXT PRIMARY KEY,
    opened_at REAL NOT NULL
);

-- === what a plan has left =================================================

CREATE TABLE IF NOT EXISTS account_usage_snapshots(
    harness TEXT NOT NULL,
    account_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    captured_at REAL NOT NULL,
    PRIMARY KEY(harness, account_id)
);

CREATE TABLE IF NOT EXISTS account_usage_windows(
    harness TEXT NOT NULL,
    account_id TEXT NOT NULL,
    window_key TEXT NOT NULL,
    used_percent TEXT NOT NULL,
    resets_at REAL,
    PRIMARY KEY(harness, account_id, window_key),
    FOREIGN KEY(harness, account_id)
        REFERENCES account_usage_snapshots(harness, account_id) ON DELETE CASCADE
);

-- === what the browser attached ============================================

CREATE TABLE IF NOT EXISTS uploads(
    upload_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    name TEXT NOT NULL,
    media_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    stored_path TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS index_uploads_by_age ON uploads(created_at);
"""


AUDIT_SCHEMA = _SCHEMA_VERSION_TABLE + """
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
