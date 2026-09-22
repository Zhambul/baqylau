-- Main schema 26 from repository/impl/sqlite/schema.py at commit 6a9e497.

CREATE TABLE IF NOT EXISTS schema_version(
    id INTEGER PRIMARY KEY CHECK(id = 1),
    version INTEGER NOT NULL,
    applied_at REAL NOT NULL
);

-- === raw events and canonical facts =======================================

CREATE TABLE IF NOT EXISTS sessions(
    session_id TEXT PRIMARY KEY,
    lead_actor_id TEXT NOT NULL,
    harness TEXT NOT NULL,
    harness_session_id TEXT NOT NULL,
    source_reference TEXT NOT NULL,
    working_directory TEXT,
    project_directory TEXT,
    terminal_window_id TEXT,
    harness_process_id INTEGER,
    created_at REAL NOT NULL,
    lifecycle TEXT NOT NULL DEFAULT 'running'
        CHECK(lifecycle IN ('running', 'finished'))
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
    payload_codec TEXT NOT NULL DEFAULT 'identity'
        CHECK(payload_codec IN ('identity', 'zlib')),
    terminal_window_id TEXT,
    harness_process_id INTEGER,
    account_id TEXT,
    account_display_name TEXT
);

CREATE INDEX IF NOT EXISTS index_raw_by_source
    ON raw_events(source_identity, id);

CREATE INDEX IF NOT EXISTS index_raw_by_session
    ON raw_events(session_id, observed_at);

-- The interpreter's durable input queue. The integer primary key preserves raw
-- arrival order and makes an empty backlog an O(1) read. It is not derivable on
-- each tick: doing that was a full scan of an ever-growing history table.
CREATE TABLE IF NOT EXISTS pending_raw_events(
    raw_event_row_id INTEGER PRIMARY KEY,
    raw_event_id TEXT NOT NULL UNIQUE,
    FOREIGN KEY(raw_event_id) REFERENCES raw_events(raw_event_id)
        ON DELETE CASCADE
);

-- Model-backed titles are not generated on the interpretation or reaction
-- critical path. The key is the durable exactly-once boundary across duplicate
-- prompt facts and daemon restarts.
CREATE TABLE IF NOT EXISTS naming_jobs(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_key TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    prompt TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('pending', 'running', 'completed', 'failed')),
    title TEXT,
    error TEXT
);

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

CREATE TRIGGER IF NOT EXISTS sessions_lifecycle_after_event
AFTER INSERT ON canonical_events
WHEN NEW.event_type IN ('session.started', 'session.finished')
BEGIN
    UPDATE sessions
    SET lifecycle = CASE NEW.event_type
        WHEN 'session.finished' THEN 'finished'
        ELSE 'running'
    END
    WHERE session_id = NEW.session_id;
END;

CREATE TRIGGER IF NOT EXISTS sessions_lifecycle_after_insert
AFTER INSERT ON sessions
BEGIN
    UPDATE sessions
    SET lifecycle = COALESCE((
        SELECT CASE canonical_events.event_type
            WHEN 'session.finished' THEN 'finished'
            ELSE 'running'
        END
        FROM canonical_events
        WHERE canonical_events.session_id = NEW.session_id
          AND canonical_events.event_type IN ('session.started', 'session.finished')
        ORDER BY canonical_events.cursor DESC
        LIMIT 1
    ), 'running')
    WHERE session_id = NEW.session_id;
END;

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

CREATE TABLE IF NOT EXISTS shell_output(
    session_id TEXT NOT NULL,
    shell_id TEXT NOT NULL,
    harness TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    parent_actor_id TEXT,
    source_path TEXT NOT NULL,
    chunk_source_type TEXT NOT NULL,
    delete_source INTEGER NOT NULL,
    initial_size INTEGER NOT NULL,
    initial_modified_at INTEGER NOT NULL,
    wait_for_source_change INTEGER NOT NULL,
    until TEXT NOT NULL CHECK(until IN ('shell_finished', 'session_finished')),
    state TEXT NOT NULL CHECK(state IN ('active', 'finishing')),
    created_at REAL NOT NULL,
    PRIMARY KEY(session_id, shell_id, source_path)
);

-- === the read model ========================================================
--
-- What every frontend reads, and the only thing they read. Written at push time
-- by the writers behind `SessionDataRepository`; `revision` and
-- `session_entries.cursor` use the canonical event cursor, so "everything
-- after cursor C" is one question with one answer across both kinds of change.

CREATE TABLE IF NOT EXISTS session_data(
    session_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL,
    payload TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS index_session_data_revision
    ON session_data(revision);

CREATE TABLE IF NOT EXISTS session_data_actors(
    session_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY(session_id, actor_id)
);

CREATE INDEX IF NOT EXISTS index_session_data_actors_revision
    ON session_data_actors(session_id, revision);

CREATE INDEX IF NOT EXISTS index_session_data_actors_global
    ON session_data_actors(revision);

CREATE TABLE IF NOT EXISTS session_entries(
    cursor INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    entry_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    parent_actor_id TEXT,
    turn_id TEXT,
    occurred_at REAL,
    summary TEXT,
    payload TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS index_session_entries_session
    ON session_entries(session_id, cursor, occurred_at);

-- The reaction loop's high-water mark against canonical_events; one row,
-- typed, the same standing as schema_version — not a key-value table.
CREATE TABLE IF NOT EXISTS reaction_progress(
    id INTEGER PRIMARY KEY CHECK(id = 1),
    canonical_cursor INTEGER NOT NULL,
    updated_at REAL NOT NULL
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
    request_id TEXT NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY(session_id, position),
    FOREIGN KEY(session_id) REFERENCES session_workspaces(session_id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS index_composer_queue_request
ON composer_queue_items(session_id, request_id);

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

CREATE TABLE IF NOT EXISTS goal_dismissals(
    session_id TEXT PRIMARY KEY,
    objective TEXT NOT NULL,
    dismissed_cursor INTEGER NOT NULL
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
