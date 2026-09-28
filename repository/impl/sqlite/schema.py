# Copyright (c) 2026 Zhambyl Yermagambet
"""Every table we own, in one file, with one version per database.

Two databases, and the reason each is its own file is in `core/data.py`.

The write split inside `main.db` is the design: `sessions` is written only by
the interpreter's session-upsert reaction (birth and upkeep derive from
committed facts), `raw_events` by any recorder, and the interpretation tables
only by the interpreter. A pulled harness source resumes from the
`source_position` of the last core raw event carrying its
`source_identity`, so recorded progress can never drift from the raw events.

There is no key-value table. Nine preference entities that used to be JSON
blobs under nine keys have nine tables with real primary keys; the queue, the
dialog answers and the usage windows are rows rather than encoded lists. Eight
opaque columns in the original stores remain deliberate: `canonical_events.payload` is the
canonical fact body, closed and versioned by `repository/mapper/facts.py`;
`raw_events.payload` restores to the verbatim bytes we observed, which is the
whole point of keeping it; `state_files.content` is a free-form audit blob written by a
facade whose contract is "record anything, never raise"; and the three read-model
payloads (`session_data`, `session_data_actors`, `session_entries`) are closed
typed documents of `domain/sessiondata.py` and `domain/entries.py`, validated on
the way in and out the same way — a column per field would be a hundred
columns, half of them null, and none of them queried. The two extension manifest
columns store checked public package declarations, including bundled schemas.
They keep current discovery and retained package history separate.
The lifecycle selection, proposal, failure, and override columns also contain
closed host models. They retain exact operation input and recovery history.
Scoped extension input uses `raw_events.extension_metadata` for one closed
metadata model. Its original document uses the existing lossless byte column.
Generated origin, owner, and scope columns support indexed mixed input reads.
"""

from __future__ import annotations

from types import MappingProxyType

MAIN_SCHEMA_VERSION = 41
AUDIT_SCHEMA_VERSION = 1
TOOL_COUNTS_REPAIR_VERSION = 15
FIRST_REPEATED_REPAIR_VERSION = 21
SECOND_REPEATED_REPAIR_VERSION = 22
SHELL_OUTPUT_REPAIR_VERSION = 6
RESTART_SHELL_REPAIR_VERSION = 26

_RAW_EVENT_TABLE = """
CREATE TABLE IF NOT EXISTS raw_events(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_event_id TEXT NOT NULL UNIQUE,
    session_id TEXT,
    harness TEXT,
    source_type TEXT NOT NULL,
    source_identity TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_position TEXT NOT NULL,
    actor_id TEXT,
    parent_actor_id TEXT,
    observed_at REAL NOT NULL,
    encoding TEXT NOT NULL,
    payload BLOB NOT NULL,
    payload_codec TEXT NOT NULL DEFAULT 'identity'
        CHECK(payload_codec IN ('identity', 'zlib')),
    terminal_window_id TEXT,
    harness_process_id INTEGER,
    account_id TEXT,
    account_display_name TEXT,
    extension_metadata TEXT,
    origin TEXT GENERATED ALWAYS AS (
        CASE WHEN extension_metadata IS NULL THEN 'harness' ELSE 'extension' END
    ) STORED,
    source_owner TEXT GENERATED ALWAYS AS (
        CASE WHEN extension_metadata IS NULL THEN harness
        ELSE json_extract(extension_metadata, '$.schema_ref.owner') END
    ) STORED,
    scope TEXT GENERATED ALWAYS AS (
        CASE WHEN extension_metadata IS NULL THEN json_object(
            'kind', 'session', 'session_id', session_id, 'actor_id', actor_id, 'harness', harness
        ) ELSE json_extract(extension_metadata, '$.scope') END
    ) STORED,
    CHECK(
        (extension_metadata IS NULL AND session_id IS NOT NULL AND harness IS NOT NULL AND actor_id IS NOT NULL)
        OR (extension_metadata IS NOT NULL AND json_valid(extension_metadata)
            AND source_owner IS NOT NULL AND scope IS NOT NULL
            AND session_id IS NULL AND harness IS NULL AND actor_id IS NULL AND parent_actor_id IS NULL
            AND terminal_window_id IS NULL AND harness_process_id IS NULL
            AND account_id IS NULL AND account_display_name IS NULL AND encoding = 'json')
    )
)
"""

_RAW_EVENT_INDEXES = (
    "CREATE INDEX IF NOT EXISTS index_raw_by_source ON raw_events(source_identity, id)",
    "CREATE INDEX IF NOT EXISTS index_raw_by_session ON raw_events(session_id, observed_at)",
    "CREATE INDEX IF NOT EXISTS index_raw_by_scope ON raw_events(scope, id)",
    "CREATE INDEX IF NOT EXISTS index_raw_owner_source ON raw_events(source_owner, scope, source_identity, id)",
)

_PENDING_RAW_TABLE = """
CREATE TABLE IF NOT EXISTS pending_raw_events(
    raw_event_row_id INTEGER PRIMARY KEY,
    raw_event_id TEXT NOT NULL UNIQUE,
    FOREIGN KEY(raw_event_id) REFERENCES raw_events(raw_event_id) ON DELETE CASCADE
)
"""

_INTERPRETATION_TABLE = """
CREATE TABLE IF NOT EXISTS interpretations(
    raw_event_id TEXT PRIMARY KEY,
    translator_version TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(
        decision IN ('translated', 'ignored_unknown', 'ignored_nonsemantic', 'translation_failed')
    ),
    reason TEXT,
    completed_at REAL NOT NULL,
    FOREIGN KEY(raw_event_id) REFERENCES raw_events(raw_event_id) ON DELETE CASCADE
)
"""

_INTERPRETATION_LINK_TABLE = """
CREATE TABLE IF NOT EXISTS interpretation_events(
    event_id TEXT NOT NULL,
    raw_event_id TEXT NOT NULL,
    event_order INTEGER NOT NULL,
    storage_result TEXT NOT NULL CHECK(storage_result IN ('accepted', 'deduplicated')),
    PRIMARY KEY(event_id, raw_event_id),
    UNIQUE(raw_event_id, event_order),
    FOREIGN KEY(event_id) REFERENCES canonical_events(event_id) ON DELETE CASCADE,
    FOREIGN KEY(raw_event_id) REFERENCES raw_events(raw_event_id) ON DELETE CASCADE
)
"""

_SCOPED_RAW_MIGRATION = (
    "CREATE TEMP TABLE migration_raw AS SELECT * FROM raw_events",
    "CREATE TEMP TABLE migration_pending AS SELECT * FROM pending_raw_events",
    "CREATE TEMP TABLE migration_verdicts AS SELECT * FROM interpretations",
    "CREATE TEMP TABLE migration_links AS SELECT * FROM interpretation_events",
    "CREATE TEMP TABLE migration_sequence AS SELECT seq FROM sqlite_sequence WHERE name='raw_events'",
    "DROP TABLE interpretation_events",
    "DROP TABLE interpretations",
    "DROP TABLE pending_raw_events",
    "DROP TABLE raw_events",
    _RAW_EVENT_TABLE,
    (
        "INSERT INTO raw_events(id, raw_event_id, session_id, harness, source_type, source_identity, "
        "source_name, source_position, actor_id, parent_actor_id, observed_at, encoding, payload, payload_codec, "
        "terminal_window_id, harness_process_id, account_id, account_display_name) "
        "SELECT id, raw_event_id, session_id, harness, source_type, source_identity, source_name, source_position, "
        "actor_id, parent_actor_id, observed_at, encoding, payload, payload_codec, terminal_window_id, "
        "harness_process_id, account_id, account_display_name FROM migration_raw ORDER BY id"
    ),
    "DELETE FROM sqlite_sequence WHERE name='raw_events'",
    "INSERT INTO sqlite_sequence(name, seq) SELECT 'raw_events', seq FROM migration_sequence",
    *_RAW_EVENT_INDEXES,
    _PENDING_RAW_TABLE,
    _INTERPRETATION_TABLE,
    _INTERPRETATION_LINK_TABLE,
    "INSERT INTO pending_raw_events SELECT * FROM migration_pending",
    "INSERT INTO interpretations SELECT * FROM migration_verdicts",
    "INSERT INTO interpretation_events SELECT * FROM migration_links",
    "DROP TABLE migration_links",
    "DROP TABLE migration_verdicts",
    "DROP TABLE migration_pending",
    "DROP TABLE migration_raw",
    "DROP TABLE migration_sequence",
)

_CANONICAL_HISTORY_TABLE = """
CREATE TABLE IF NOT EXISTS canonical_histories(
    history_revision TEXT PRIMARY KEY NOT NULL CHECK(length(history_revision) > 0),
    created_at REAL
)
"""

_DEFAULT_CANONICAL_HISTORY = "INSERT OR IGNORE INTO canonical_histories(history_revision) VALUES('default')"

_CANONICAL_EVENT_TABLE = """
CREATE TABLE IF NOT EXISTS canonical_events(
    cursor INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    schema_version INTEGER,
    event_type TEXT NOT NULL,
    session_id TEXT,
    actor_id TEXT,
    turn_id TEXT,
    parent_actor_id TEXT,
    harness TEXT,
    occurred_at REAL,
    terminal_window_id TEXT,
    harness_process_id INTEGER,
    accepted_at REAL NOT NULL,
    payload TEXT NOT NULL,
    history_revision TEXT NOT NULL DEFAULT 'default',
    extension_metadata TEXT,
    origin TEXT GENERATED ALWAYS AS (
        CASE WHEN extension_metadata IS NULL THEN 'core' ELSE 'extension' END
    ) STORED,
    fact_owner TEXT GENERATED ALWAYS AS (
        CASE WHEN extension_metadata IS NULL THEN 'core'
        ELSE json_extract(extension_metadata, '$.schema_ref.owner') END
    ) STORED,
    scope TEXT GENERATED ALWAYS AS (
        CASE WHEN extension_metadata IS NULL THEN json_object(
            'kind', 'session', 'session_id', session_id, 'actor_id', actor_id, 'harness', harness
        ) ELSE json_extract(extension_metadata, '$.scope') END
    ) STORED,
    UNIQUE(history_revision, event_id),
    FOREIGN KEY(history_revision) REFERENCES canonical_histories(history_revision),
    CHECK(
        (extension_metadata IS NULL AND schema_version IS NOT NULL
            AND session_id IS NOT NULL AND harness IS NOT NULL AND actor_id IS NOT NULL)
        OR (extension_metadata IS NOT NULL AND json_valid(extension_metadata)
            AND fact_owner IS NOT NULL AND scope IS NOT NULL AND schema_version IS NULL
            AND session_id IS NULL AND harness IS NULL AND actor_id IS NULL
            AND turn_id IS NULL AND parent_actor_id IS NULL
            AND terminal_window_id IS NULL AND harness_process_id IS NULL)
    )
)
"""

_CANONICAL_EVENT_INDEXES = (
    "CREATE INDEX IF NOT EXISTS index_canonical_session_type ON canonical_events(session_id, event_type, cursor)",
    "CREATE INDEX IF NOT EXISTS index_canonical_session_actor ON canonical_events(session_id, actor_id, cursor)",
    "CREATE INDEX IF NOT EXISTS index_canonical_session_cursor ON canonical_events(session_id, cursor)",
    "CREATE INDEX IF NOT EXISTS index_canonical_history_cursor ON canonical_events(history_revision, cursor)",
    "CREATE INDEX IF NOT EXISTS index_canonical_history_scope ON canonical_events(history_revision, scope, cursor)",
)

_VERSIONED_INTERPRETATION_TABLE = """
CREATE TABLE IF NOT EXISTS interpretations(
    raw_event_id TEXT NOT NULL,
    translator_version TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(
        decision IN ('translated', 'ignored_unknown', 'ignored_nonsemantic', 'translation_failed', 'suppressed')
    ),
    reason TEXT,
    completed_at REAL NOT NULL,
    history_revision TEXT NOT NULL DEFAULT 'default',
    runtime_revision TEXT,
    PRIMARY KEY(history_revision, raw_event_id),
    FOREIGN KEY(raw_event_id) REFERENCES raw_events(raw_event_id) ON DELETE CASCADE,
    FOREIGN KEY(history_revision) REFERENCES canonical_histories(history_revision)
)
"""

_VERSIONED_INTERPRETATION_LINK_TABLE = """
CREATE TABLE IF NOT EXISTS interpretation_events(
    event_id TEXT NOT NULL,
    raw_event_id TEXT NOT NULL,
    event_order INTEGER NOT NULL,
    storage_result TEXT NOT NULL CHECK(storage_result IN ('accepted', 'deduplicated')),
    history_revision TEXT NOT NULL DEFAULT 'default',
    PRIMARY KEY(history_revision, event_id, raw_event_id),
    UNIQUE(history_revision, raw_event_id, event_order),
    FOREIGN KEY(history_revision, event_id) REFERENCES canonical_events(history_revision, event_id) ON DELETE CASCADE,
    FOREIGN KEY(raw_event_id) REFERENCES raw_events(raw_event_id) ON DELETE CASCADE
)
"""

# Publication remains closed until history and projection heads can switch
# together. These views select the existing live history, never a candidate.
_CURRENT_HISTORY_VIEWS = (
    (
        "CREATE VIEW IF NOT EXISTS current_canonical_events AS SELECT * FROM canonical_events "
        "WHERE history_revision='default'"
    ),
    (
        "CREATE VIEW IF NOT EXISTS current_interpretations AS SELECT * FROM interpretations "
        "WHERE history_revision='default'"
    ),
    (
        "CREATE VIEW IF NOT EXISTS current_interpretation_events AS SELECT * FROM interpretation_events "
        "WHERE history_revision='default'"
    ),
)

_CURRENT_LIFECYCLE_TRIGGERS = (
    """
    CREATE TRIGGER IF NOT EXISTS sessions_lifecycle_after_event
    AFTER INSERT ON canonical_events
    WHEN NEW.history_revision = 'default' AND NEW.origin = 'core'
      AND NEW.event_type IN ('session.started', 'session.finished')
    BEGIN
        UPDATE sessions
        SET lifecycle = CASE NEW.event_type
            WHEN 'session.finished' THEN 'finished'
            ELSE 'running'
        END
        WHERE session_id = NEW.session_id;
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS sessions_lifecycle_after_insert
    AFTER INSERT ON sessions
    BEGIN
        UPDATE sessions
        SET lifecycle = COALESCE((
            SELECT CASE event_type
                WHEN 'session.finished' THEN 'finished'
                ELSE 'running'
            END
            FROM current_canonical_events
            WHERE session_id = NEW.session_id AND origin = 'core'
              AND event_type IN ('session.started', 'session.finished')
            ORDER BY cursor DESC LIMIT 1
        ), 'running')
        WHERE session_id = NEW.session_id;
    END
    """,
)

_VERSIONED_CANONICAL_MIGRATION = (
    "CREATE TEMP TABLE migration_canonical AS SELECT * FROM canonical_events",
    "CREATE TEMP TABLE migration_verdicts AS SELECT * FROM interpretations",
    "CREATE TEMP TABLE migration_links AS SELECT * FROM interpretation_events",
    "CREATE TEMP TABLE migration_sequence AS SELECT seq FROM sqlite_sequence WHERE name='canonical_events'",
    "DROP TRIGGER IF EXISTS sessions_lifecycle_after_event",
    "DROP TRIGGER IF EXISTS sessions_lifecycle_after_insert",
    "DROP TABLE interpretation_events",
    "DROP TABLE interpretations",
    "DROP TABLE canonical_events",
    _CANONICAL_HISTORY_TABLE,
    _DEFAULT_CANONICAL_HISTORY,
    _CANONICAL_EVENT_TABLE,
    (
        "INSERT INTO canonical_events(cursor, event_id, schema_version, event_type, session_id, actor_id, turn_id, "
        "parent_actor_id, harness, occurred_at, terminal_window_id, harness_process_id, accepted_at, payload) "
        "SELECT * FROM migration_canonical ORDER BY cursor"
    ),
    "DELETE FROM sqlite_sequence WHERE name='canonical_events'",
    "INSERT INTO sqlite_sequence(name, seq) SELECT 'canonical_events', seq FROM migration_sequence",
    *_CANONICAL_EVENT_INDEXES,
    _VERSIONED_INTERPRETATION_TABLE,
    _VERSIONED_INTERPRETATION_LINK_TABLE,
    (
        "INSERT INTO interpretations(raw_event_id, translator_version, decision, reason, completed_at) "
        "SELECT * FROM migration_verdicts"
    ),
    (
        "INSERT INTO interpretation_events(event_id, raw_event_id, event_order, storage_result) "
        "SELECT * FROM migration_links"
    ),
    *_CURRENT_HISTORY_VIEWS,
    *_CURRENT_LIFECYCLE_TRIGGERS,
    "DROP TABLE migration_links",
    "DROP TABLE migration_verdicts",
    "DROP TABLE migration_canonical",
    "DROP TABLE migration_sequence",
)

_NORMALIZED_JOURNAL_TABLES = (
    """
    CREATE TABLE IF NOT EXISTS interpretation_bodies(
        digest TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        byte_length INTEGER NOT NULL CHECK(byte_length >= 0),
        body TEXT NOT NULL,
        created_at REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS interpretation_journal_steps(
        history_revision TEXT NOT NULL,
        raw_event_id TEXT NOT NULL,
        step_index INTEGER NOT NULL,
        stage TEXT NOT NULL,
        step TEXT NOT NULL,
        PRIMARY KEY(history_revision, raw_event_id, step_index),
        FOREIGN KEY(history_revision, raw_event_id)
            REFERENCES interpretations(history_revision, raw_event_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS interpretation_journal_bodies(
        history_revision TEXT NOT NULL,
        raw_event_id TEXT NOT NULL,
        digest TEXT NOT NULL,
        PRIMARY KEY(history_revision, raw_event_id, digest),
        FOREIGN KEY(history_revision, raw_event_id)
            REFERENCES interpretations(history_revision, raw_event_id) ON DELETE CASCADE,
        FOREIGN KEY(digest) REFERENCES interpretation_bodies(digest)
    )
    """,
)

_INTERPRETATION_STEP_VIEWS = (
    """
    CREATE VIEW IF NOT EXISTS interpretation_steps AS
    SELECT journal.history_revision, journal.raw_event_id, CAST(step.key AS INTEGER) AS step_index,
        json_extract(step.value, '$.stage') AS stage, step.value AS step
    FROM interpretation_journals AS journal, json_each(journal.proposal, '$.steps') AS step
    """,
    """
    CREATE TABLE IF NOT EXISTS extension_translation_state(
        extension_id TEXT NOT NULL,
        history_revision TEXT NOT NULL,
        scope TEXT NOT NULL,
        source_identity TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision > 0),
        document TEXT,
        PRIMARY KEY(extension_id, history_revision, scope, source_identity),
        FOREIGN KEY(history_revision) REFERENCES canonical_histories(history_revision)
    )
    """,
)

_INTERPRETATION_JOURNAL_TABLES = (
    """
    CREATE TABLE IF NOT EXISTS interpretation_journals(
        history_revision TEXT NOT NULL,
        raw_event_id TEXT NOT NULL,
        proposal TEXT NOT NULL,
        codec_version INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY(history_revision, raw_event_id),
        FOREIGN KEY(history_revision, raw_event_id)
            REFERENCES interpretations(history_revision, raw_event_id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS index_journal_raw_history ON interpretation_journals(raw_event_id, history_revision)",
    *_INTERPRETATION_STEP_VIEWS,
    *_NORMALIZED_JOURNAL_TABLES,
)

# Migration 32 creates the journal before the normalized codec exists. Keep its
# exact original shape so migration 35 can add the codec column to every older
# database, and so a fresh schema body and a migrated one agree.
_INTERPRETATION_JOURNAL_MIGRATION_V32 = (
    """
    CREATE TABLE IF NOT EXISTS interpretation_journals(
        history_revision TEXT NOT NULL,
        raw_event_id TEXT NOT NULL,
        proposal TEXT NOT NULL,
        PRIMARY KEY(history_revision, raw_event_id),
        FOREIGN KEY(history_revision, raw_event_id)
            REFERENCES interpretations(history_revision, raw_event_id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS index_journal_raw_history ON interpretation_journals(raw_event_id, history_revision)",
    *_INTERPRETATION_STEP_VIEWS,
)

_NORMALIZED_JOURNAL_MIGRATION = (
    "ALTER TABLE interpretation_journals ADD COLUMN codec_version INTEGER NOT NULL DEFAULT 1",
    *_NORMALIZED_JOURNAL_TABLES,
)

_EXTENSION_RECORD_TABLES = (
    """
    CREATE TABLE IF NOT EXISTS extension_records(
        owner TEXT NOT NULL,
        collection TEXT NOT NULL,
        record_key TEXT NOT NULL,
        scope TEXT NOT NULL CHECK(json_valid(scope)),
        state TEXT NOT NULL CHECK(state IN ('stored', 'deleted')),
        revision INTEGER NOT NULL CHECK(revision >= 1),
        projection_revision TEXT NOT NULL,
        schema_ref TEXT NOT NULL CHECK(json_valid(schema_ref)),
        document TEXT,
        summary TEXT,
        scope_kind TEXT GENERATED ALWAYS AS (json_extract(scope, '$.kind')) STORED,
        session_id TEXT GENERATED ALWAYS AS (json_extract(scope, '$.session_id')) STORED,
        repository_id TEXT GENERATED ALWAYS AS (json_extract(scope, '$.repository_id')) STORED,
        PRIMARY KEY(owner, collection, scope, record_key),
        CHECK(
            (state = 'stored' AND document IS NOT NULL AND summary IS NOT NULL)
            OR (state = 'deleted' AND document IS NULL AND summary IS NULL)
        )
    )
    """,
    (
        "CREATE INDEX IF NOT EXISTS index_extension_records_scope "
        "ON extension_records(owner, collection, scope_kind, session_id, record_key)"
    ),
    """
    CREATE TABLE IF NOT EXISTS extension_projection_cursors(
        owner TEXT NOT NULL,
        scope TEXT NOT NULL CHECK(json_valid(scope)),
        history_revision TEXT NOT NULL,
        generation TEXT NOT NULL,
        commit_cursor INTEGER NOT NULL CHECK(commit_cursor >= 0),
        updated_at REAL NOT NULL,
        PRIMARY KEY(owner, scope, history_revision, generation)
    )
    """,
)

_SESSION_ENTRY_COMMIT_MIGRATION = (
    """
    CREATE TABLE session_entries_with_commits(
        cursor INTEGER PRIMARY KEY AUTOINCREMENT,
        commit_cursor INTEGER NOT NULL,
        position INTEGER NOT NULL DEFAULT 0,
        entry_id TEXT NOT NULL UNIQUE,
        session_id TEXT NOT NULL,
        entry_type TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        parent_actor_id TEXT,
        turn_id TEXT,
        occurred_at REAL,
        summary TEXT,
        payload TEXT NOT NULL
    )
    """,
    """
    INSERT INTO session_entries_with_commits(
        cursor, commit_cursor, position, entry_id, session_id, entry_type,
        actor_id, parent_actor_id, turn_id, occurred_at, summary, payload
    )
    SELECT cursor, cursor, 0, entry_id, session_id, entry_type,
           actor_id, parent_actor_id, turn_id, occurred_at, summary, payload
    FROM session_entries
    """,
    "DROP TABLE session_entries",
    "ALTER TABLE session_entries_with_commits RENAME TO session_entries",
    "CREATE INDEX index_session_entries_session ON session_entries(session_id, cursor, occurred_at)",
    "CREATE INDEX index_session_entries_commit ON session_entries(commit_cursor, position)",
    *_EXTENSION_RECORD_TABLES,
)

_EXTENSION_SOURCE_TABLES = (
    """
    CREATE TABLE IF NOT EXISTS extension_source_reads(
        runtime_revision TEXT NOT NULL,
        call_id TEXT NOT NULL,
        proposal TEXT NOT NULL,
        observed_at REAL NOT NULL,
        PRIMARY KEY(runtime_revision, call_id),
        FOREIGN KEY(runtime_revision) REFERENCES extension_runtime_revisions(runtime_revision)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS extension_source_checkpoints(
        extension_id TEXT NOT NULL,
        scope TEXT NOT NULL,
        source_identity TEXT NOT NULL,
        source_type TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision > 0),
        position TEXT NOT NULL,
        runtime_revision TEXT NOT NULL,
        call_id TEXT NOT NULL,
        PRIMARY KEY(extension_id, scope, source_identity),
        FOREIGN KEY(runtime_revision, call_id) REFERENCES extension_source_reads(runtime_revision, call_id)
    )
    """,
)

# Discovery records use typed manifests. They do not store active workers or
# arbitrary preference keys. Retained manifests keep schema declarations after
# a source folder is removed. Runtime and settings storage follow separately.
_EXTENSION_CATALOG_TABLES = (
    """
    CREATE TABLE IF NOT EXISTS extension_catalog_head(
        id INTEGER PRIMARY KEY CHECK(id = 1),
        revision INTEGER NOT NULL CHECK(revision >= 0)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS extension_packages(
        source_path TEXT PRIMARY KEY,
        resolved_path TEXT,
        package_digest TEXT,
        extension_id TEXT,
        manifest TEXT,
        issue_code TEXT,
        issue_detail TEXT,
        CHECK((issue_code IS NULL) = (issue_detail IS NULL)),
        CHECK(issue_code IS NOT NULL OR (
            resolved_path IS NOT NULL AND package_digest IS NOT NULL AND manifest IS NOT NULL
        ))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS extension_catalog_errors(
        root_path TEXT PRIMARY KEY,
        issue_code TEXT NOT NULL,
        issue_detail TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS extension_package_manifests(
        package_digest TEXT PRIMARY KEY,
        manifest TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS index_extension_packages_owner ON extension_packages(extension_id)",
)


# Runtime selections, operation proposals, and raw settings overrides are
# closed host models. They contain no worker objects or untyped preference map.
_EXTENSION_LIFECYCLE_TABLES = (
    """
    CREATE TABLE IF NOT EXISTS extension_runtime_revisions(
        runtime_revision TEXT PRIMARY KEY,
        selection TEXT NOT NULL,
        committed_at REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS extension_lifecycle_operations(
        operation_id TEXT PRIMARY KEY,
        manager_id TEXT NOT NULL,
        runtime_revision TEXT NOT NULL UNIQUE,
        accepted_revision INTEGER NOT NULL CHECK(accepted_revision > 0),
        status TEXT NOT NULL CHECK(status IN ('preparing', 'succeeded', 'failed', 'interrupted')),
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL CHECK(updated_at >= created_at),
        proposal TEXT NOT NULL,
        failure TEXT,
        CHECK((status IN ('failed', 'interrupted')) = (failure IS NOT NULL)),
        FOREIGN KEY(runtime_revision) REFERENCES extension_runtime_revisions(runtime_revision)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS extension_lifecycle_head(
        id INTEGER PRIMARY KEY CHECK(id = 1),
        revision INTEGER NOT NULL CHECK(revision >= 0),
        manager_id TEXT NOT NULL,
        committed_runtime TEXT,
        pending_operation TEXT,
        FOREIGN KEY(committed_runtime) REFERENCES extension_runtime_revisions(runtime_revision),
        FOREIGN KEY(pending_operation) REFERENCES extension_lifecycle_operations(operation_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS extension_requests(
        extension_id TEXT PRIMARY KEY,
        enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
        package_digest TEXT,
        CHECK(enabled = 0 OR package_digest IS NOT NULL),
        FOREIGN KEY(package_digest) REFERENCES extension_package_manifests(package_digest)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS extension_settings(
        extension_id TEXT PRIMARY KEY,
        overrides TEXT NOT NULL
    )
    """,
)

_EXTENSION_SHUTDOWN_TABLES = (
    """
    CREATE TABLE IF NOT EXISTS extension_shutdown_records(
        cursor INTEGER PRIMARY KEY AUTOINCREMENT,
        record_id TEXT NOT NULL UNIQUE,
        manager_id TEXT NOT NULL,
        recorded_at REAL NOT NULL,
        observation TEXT NOT NULL CHECK(json_valid(observation))
    )
    """,
)

_EXTENSION_JOB_TABLES = (
    """
    CREATE TABLE IF NOT EXISTS extension_jobs(
        owner TEXT NOT NULL,
        scope TEXT NOT NULL CHECK(json_valid(scope)),
        job_id TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('command', 'observer')),
        request_key TEXT,
        cause_event_id TEXT,
        state TEXT NOT NULL CHECK(
            state IN ('accepted', 'running', 'succeeded', 'failed', 'canceled', 'outcome_unknown')
        ),
        revision INTEGER NOT NULL CHECK(revision >= 1),
        binding TEXT NOT NULL CHECK(json_valid(binding)),
        request TEXT NOT NULL CHECK(json_valid(request)),
        result TEXT CHECK(result IS NULL OR json_valid(result)),
        diagnostic TEXT CHECK(diagnostic IS NULL OR json_valid(diagnostic)),
        consumer_cursor INTEGER,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        scope_kind TEXT GENERATED ALWAYS AS (json_extract(scope, '$.kind')) STORED,
        session_id TEXT GENERATED ALWAYS AS (json_extract(scope, '$.session_id')) STORED,
        repository_id TEXT GENERATED ALWAYS AS (json_extract(scope, '$.repository_id')) STORED,
        PRIMARY KEY(owner, scope, job_id),
        CHECK(
            (kind = 'command' AND request_key IS NOT NULL AND cause_event_id IS NULL)
            OR (kind = 'observer' AND request_key IS NULL AND cause_event_id IS NOT NULL)
        )
    )
    """,
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS index_extension_jobs_command_request "
        "ON extension_jobs(owner, scope, request_key) WHERE kind = 'command'"
    ),
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS index_extension_jobs_observer_cause "
        "ON extension_jobs(owner, scope, cause_event_id) WHERE kind = 'observer'"
    ),
    (
        "CREATE INDEX IF NOT EXISTS index_extension_jobs_scope "
        "ON extension_jobs(owner, scope_kind, session_id, state)"
    ),
)

_EXTENSION_OBSERVER_CURSOR_TABLES = (
    """
    CREATE TABLE IF NOT EXISTS extension_observer_cursors(
        owner TEXT NOT NULL,
        scope TEXT NOT NULL CHECK(json_valid(scope)),
        history_revision TEXT NOT NULL,
        generation TEXT NOT NULL,
        commit_cursor INTEGER NOT NULL CHECK(commit_cursor >= 0),
        updated_at REAL NOT NULL,
        PRIMARY KEY(owner, scope, history_revision, generation)
    )
    """,
)

_CANONICAL_SCOPE_HEAD_TABLES = (
    """
    CREATE TABLE IF NOT EXISTS canonical_scope_heads(
        history_revision TEXT NOT NULL,
        scope TEXT NOT NULL CHECK(json_valid(scope)),
        head_cursor INTEGER NOT NULL CHECK(head_cursor >= 1),
        scope_kind TEXT GENERATED ALWAYS AS (json_extract(scope, '$.kind')) STORED,
        PRIMARY KEY(history_revision, scope)
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS canonical_scope_head_after_event
    AFTER INSERT ON canonical_events
    BEGIN
        INSERT INTO canonical_scope_heads(history_revision, scope, head_cursor)
        VALUES(NEW.history_revision, NEW.scope, NEW.cursor)
        ON CONFLICT(history_revision, scope) DO UPDATE SET head_cursor=MAX(head_cursor, excluded.head_cursor);
    END
    """,
)

_EXTENSION_JOB_STATE_INDEX = (
    "CREATE INDEX IF NOT EXISTS index_extension_jobs_state ON extension_jobs(state, updated_at, job_id)"
)

_CANONICAL_SCOPE_HEAD_MIGRATION = (
    *_CANONICAL_SCOPE_HEAD_TABLES,
    _EXTENSION_JOB_STATE_INDEX,
    """
    INSERT OR REPLACE INTO canonical_scope_heads(history_revision, scope, head_cursor)
    SELECT history_revision, scope, MAX(cursor) FROM canonical_events GROUP BY history_revision, scope
    """,
)

_EXTENSION_HEALTH_TABLES = (
    """
    CREATE TABLE IF NOT EXISTS extension_health(
        extension_id TEXT PRIMARY KEY,
        state TEXT NOT NULL CHECK(state IN ('healthy', 'failing', 'failed')),
        consecutive_failures INTEGER NOT NULL CHECK(consecutive_failures >= 0),
        last_failure_where TEXT,
        last_failure_at REAL,
        last_success_at REAL
    )
    """,
)

_EXTENSION_PROJECTION_GENERATION_TABLES = (
    """
    CREATE TABLE IF NOT EXISTS history_reprocessings(
        history_revision TEXT PRIMARY KEY REFERENCES canonical_histories(history_revision),
        session_id TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('building', 'ready', 'switching', 'active', 'retired', 'failed')),
        replay_cursor INTEGER NOT NULL CHECK(replay_cursor >= 0),
        live_head INTEGER NOT NULL CHECK(live_head >= 0),
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        comparison TEXT CHECK(comparison IS NULL OR json_valid(comparison)),
        diagnostic TEXT CHECK(diagnostic IS NULL OR json_valid(diagnostic))
    )
    """,
    "CREATE INDEX IF NOT EXISTS index_history_reprocessings_state ON history_reprocessings(state, created_at)",
    """
    CREATE TABLE IF NOT EXISTS extension_consumer_floors(
        consumer TEXT NOT NULL CHECK(consumer IN ('projection', 'observer')),
        owner TEXT NOT NULL,
        history_revision TEXT NOT NULL,
        generation TEXT NOT NULL,
        floor_cursor INTEGER NOT NULL CHECK(floor_cursor >= 0),
        created_at REAL NOT NULL,
        PRIMARY KEY(consumer, owner, history_revision, generation)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS read_model_views(
        id INTEGER PRIMARY KEY CHECK(id = 1),
        revision INTEGER NOT NULL CHECK(revision >= 0)
    )
    """,
    "INSERT OR IGNORE INTO read_model_views(id, revision) VALUES(1, 0)",
    """
    CREATE TABLE IF NOT EXISTS extension_projection_generations(
        generation TEXT PRIMARY KEY,
        owner TEXT NOT NULL,
        history_revision TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('building', 'migrating', 'ready', 'active', 'retired', 'failed')),
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        comparison TEXT CHECK(comparison IS NULL OR json_valid(comparison)),
        diagnostic TEXT CHECK(diagnostic IS NULL OR json_valid(diagnostic))
    )
    """,
    (
        "CREATE INDEX IF NOT EXISTS index_extension_projection_generations_owner "
        "ON extension_projection_generations(owner, state)"
    ),
    """
    CREATE TABLE IF NOT EXISTS extension_projection_heads(
        owner TEXT PRIMARY KEY,
        generation TEXT NOT NULL REFERENCES extension_projection_generations(generation),
        switched_at REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS extension_candidate_records(
        generation TEXT NOT NULL REFERENCES extension_projection_generations(generation),
        owner TEXT NOT NULL,
        collection TEXT NOT NULL,
        record_key TEXT NOT NULL,
        scope TEXT NOT NULL CHECK(json_valid(scope)),
        state TEXT NOT NULL CHECK(state IN ('stored', 'deleted')),
        revision INTEGER NOT NULL CHECK(revision >= 1),
        schema_ref TEXT NOT NULL CHECK(json_valid(schema_ref)),
        document TEXT,
        summary TEXT,
        PRIMARY KEY(generation, owner, collection, scope, record_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS extension_candidate_entries(
        generation TEXT NOT NULL REFERENCES extension_projection_generations(generation),
        owner TEXT NOT NULL,
        commit_cursor INTEGER NOT NULL,
        position INTEGER NOT NULL,
        entry_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        entry_type TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        parent_actor_id TEXT,
        turn_id TEXT,
        occurred_at REAL,
        summary TEXT,
        payload TEXT NOT NULL,
        PRIMARY KEY(generation, entry_id)
    )
    """,
    (
        "CREATE INDEX IF NOT EXISTS index_extension_candidate_entries_owner "
        "ON extension_candidate_entries(generation, owner)"
    ),
)

_EXTENSION_RESOLUTION_TABLES = (
    """
    CREATE TABLE IF NOT EXISTS extension_runtime_resolutions(
        runtime_revision TEXT PRIMARY KEY,
        resolution TEXT NOT NULL,
        FOREIGN KEY(runtime_revision) REFERENCES extension_runtime_revisions(runtime_revision)
    )
    """,
)


def _repeat_repairs(
    migrations: dict[int, tuple[str, ...]],
) -> MappingProxyType[int, tuple[str, ...]]:
    repair = migrations[TOOL_COUNTS_REPAIR_VERSION]
    migrations[FIRST_REPEATED_REPAIR_VERSION] = repair
    migrations[SECOND_REPEATED_REPAIR_VERSION] = repair
    # Native command completion after a restart could omit output completion.
    # Add the missing facts with the same repair used for older yielded shells.
    migrations[RESTART_SHELL_REPAIR_VERSION] = migrations[SHELL_OUTPUT_REPAIR_VERSION]
    return MappingProxyType(migrations)


# Version 4 rewrote the canonical vocabulary, so files older than that remain
# intentionally unsupported. Version 5 removed harness-native selection ids
# from ModelReference. Version 6 repairs Codex yielded commands recorded before
# their adapter emitted the distinct output-finished fact: adding that fact to
# the canonical log keeps both the current projection and every later rebuild
# honest. Version 7 gives each queued send its request identity, so an HTTP
# retry cannot add the same message twice. Version 8 keeps the complete goal
# state and reason in the session read model instead of one completed flag.
# Version 9 settles Codex shell results that an older ambiguous parallel-command
# correlation added after their turn had already ended.
# Version 10 gives the interpreter a durable, indexed input queue. Before this,
# every 0.25-second tick scanned all raw-event history to prove that nothing was
# waiting. The queue is written and cleared in the same transactions as the raw
# observation and its verdict, so it cannot drift from either fact.
# Version 11 records the lossless storage codec for raw observations. Old rows
# stay byte-for-byte in place as `identity`; new rows are compressed before
# it reaches SQLite and restored at the repository boundary.
# Version 12 keeps the latest session lifecycle on the session row. SQLite
# updates it in the same transaction as a canonical start or finish fact. This
# removes two correlated history reads from every interpreter tick.
# Version 13 stores the stable owner checkout observed when a session starts.
# A linked worktree can later be removed, but its project group must not change.
# Version 14 adds the durable, idempotent automatic-title job queue.
# Version 15 closes a yielded Codex shell whose native completion was recorded
# as a second shell after an application restart lost transient correlation.
# Version 16 repairs a resumed native run whose process exit deduplicated against
# an earlier run's session finish. Without the second finish, the interpreter
# keeps the dead session watchable and can replay a large rollout indefinitely.
# Version 17 makes the session activity index cover both values the session list
# aggregates. Without `occurred_at`, SQLite visited every entry table row, whose
# payload pages grow with the complete feed, to answer a three-column summary.
# Version 18 requeues ignored Claude PostToolUse hooks. TaskStop was previously
# nonsemantic, so affected background jobs have no output-finished fact. The
# current translator identifies TaskStop from the restored hook and recovers its
# shell relation from the native transcript. Other ignored hooks stay ignored.
# Version 21 runs the version 15 duplicate-shell repair again. Sessions created
# after version 15 could still hit that defect until restart correlation became
# durable in both translators.
# Version 22 accepts the observed native order: the replacement shell can finish
# before the empty wrapper marks the original shell as background work.
# Version 23 lets one shell follow several redirected output files.
# Version 24 gives stored tool counts named fields.
# Version 27 adds revisioned extension discovery and retained schema declarations.
# Version 28 adds lifecycle requests, operation history, settings, and runtime selections.
# Version 30 adds scoped extension observations to the original raw store and queue.
# Version 35 stores each journal body once by digest and keeps ordered steps as
# references. A large reply can then publish without repeated body copies
# consuming the journal budget. Codec version 1 rows keep their inline proposal.
# Version 36 lets one commit add several feed entries. `commit_cursor` keeps the
# canonical boundary for deltas and `position` orders entries inside it; the row
# `cursor` stays the paging key.
# Version 37 stores accepted command and observer jobs. A command deduplicates
# on its request key; an observer deduplicates on its cause event. Both keep a
# monotonic revision, a typed binding, and an optional final result.
# Version 38 stores the observer consumer cursor per owner, scope, history, and
# generation, written with the accepted observer job in one transaction.
# Version 39 keeps the last canonical cursor of each history scope. A trigger
# updates it on every fact insert, so a projection or observer pass can find the
# scopes after its own cursors without a scan of every fact. It also indexes
# extension jobs by state, so recovery and scheduling read one state in order.
# Version 40 adds projection generations. The live record and feed tables keep
# only the active generation of each owner. A rebuild writes a candidate
# generation into mirror tables; a switch moves rows between the two in one
# transaction, and keeps the previous generation in the mirror for recovery.
# `read_model_views` counts switches, so a session stream can reset its client.
# `extension_consumer_floors` starts a newly active live projector or observer
# at the canonical head, so enable applies to future facts; only an explicit
# rebuild reads the past. `history_reprocessings` records one closed session's
# replay into a candidate history, its comparison, and its switch.
# Version 41 keeps each extension's consecutive worker failures and health
# state, so that a failing extension stays visible across a restart.
MAIN_MIGRATIONS = _repeat_repairs({
    34: _EXTENSION_SHUTDOWN_TABLES,
    37: _EXTENSION_JOB_TABLES,
    38: _EXTENSION_OBSERVER_CURSOR_TABLES,
    39: _CANONICAL_SCOPE_HEAD_MIGRATION,
    40: _EXTENSION_PROJECTION_GENERATION_TABLES,
    41: _EXTENSION_HEALTH_TABLES,
    5: (
        """
        UPDATE session_data_actors
        SET payload = json_set(
            json_remove(payload, '$.model.native_id', '$.model.selection_id'),
            '$.model.name', json_extract(payload, '$.model.native_id')
        )
        WHERE json_type(payload, '$.model') = 'object'
          AND json_type(payload, '$.model.name') IS NULL
          AND json_type(payload, '$.model.native_id') = 'text'
        """,
    ),
    6: (
        """
        INSERT INTO canonical_events(
            event_id, schema_version, event_type, session_id, actor_id,
            turn_id, parent_actor_id, harness, occurred_at,
            terminal_window_id, harness_process_id, accepted_at, payload
        )
        SELECT
            'migration:6:shell-output-finished:' || finished.event_id,
            finished.schema_version,
            'shell.output_finished',
            finished.session_id,
            finished.actor_id,
            finished.turn_id,
            finished.parent_actor_id,
            finished.harness,
            finished.occurred_at,
            finished.terminal_window_id,
            finished.harness_process_id,
            finished.accepted_at,
            json_object(
                'shell_id', json_extract(finished.payload, '$.shell_id'),
                'outcome', json_extract(finished.payload, '$.outcome')
            )
        FROM canonical_events AS backgrounded
        JOIN canonical_events AS finished
          ON finished.session_id = backgrounded.session_id
         AND finished.actor_id = backgrounded.actor_id
         AND finished.event_type = 'shell.finished'
         AND json_extract(finished.payload, '$.shell_id') =
             json_extract(backgrounded.payload, '$.shell_id')
        WHERE backgrounded.harness = 'codex'
          AND backgrounded.event_type = 'shell.backgrounded'
          AND finished.cursor = (
              SELECT MAX(candidate.cursor)
              FROM canonical_events AS candidate
              WHERE candidate.session_id = backgrounded.session_id
                AND candidate.actor_id = backgrounded.actor_id
                AND candidate.event_type = 'shell.finished'
                AND json_extract(candidate.payload, '$.shell_id') =
                    json_extract(backgrounded.payload, '$.shell_id')
          )
          AND NOT EXISTS (
              SELECT 1
              FROM canonical_events AS closed
              WHERE closed.session_id = backgrounded.session_id
                AND closed.actor_id = backgrounded.actor_id
                AND closed.event_type = 'shell.output_finished'
                AND json_extract(closed.payload, '$.shell_id') =
                    json_extract(backgrounded.payload, '$.shell_id')
          )
        """,
    ),
    7: (
        """
        ALTER TABLE composer_queue_items
        ADD COLUMN request_id TEXT NOT NULL DEFAULT ''
        """,
        """
        UPDATE composer_queue_items
        SET request_id = 'legacy:' || position
        WHERE request_id = ''
        """,
        """
        CREATE UNIQUE INDEX index_composer_queue_request
        ON composer_queue_items(session_id, request_id)
        """,
    ),
    8: (
        """
        UPDATE session_data
        SET payload = json_set(
            json_remove(payload, '$.goal.completed'),
            '$.goal.state',
            CASE json_extract(payload, '$.goal.completed')
                WHEN 1 THEN 'completed'
                ELSE 'active'
            END,
            '$.goal.reason', NULL
        )
        WHERE json_type(payload, '$.goal') = 'object'
          AND json_type(payload, '$.goal.state') IS NULL
        """,
    ),
    9: (
        """
        INSERT INTO canonical_events(
            event_id, schema_version, event_type, session_id, actor_id,
            turn_id, parent_actor_id, harness, occurred_at,
            terminal_window_id, harness_process_id, accepted_at, payload
        )
        SELECT
            'migration:9:shell-settled:' || finished.event_id,
            finished.schema_version,
            'shell.output_finished',
            finished.session_id,
            finished.actor_id,
            finished.turn_id,
            finished.parent_actor_id,
            finished.harness,
            finished.occurred_at,
            finished.terminal_window_id,
            finished.harness_process_id,
            finished.accepted_at,
            json_object(
                'shell_id', json_extract(finished.payload, '$.shell_id'),
                'outcome', json_extract(finished.payload, '$.outcome')
            )
        FROM canonical_events AS finished
        WHERE finished.harness = 'codex'
          AND finished.event_type = 'shell.finished'
          AND finished.cursor > COALESCE((
              SELECT MAX(turn_end.cursor)
              FROM canonical_events AS turn_end
              WHERE turn_end.session_id = finished.session_id
                AND turn_end.actor_id = finished.actor_id
                AND turn_end.event_type IN ('turn.finished', 'turn.aborted')
          ), finished.cursor)
          AND NOT EXISTS (
              SELECT 1
              FROM canonical_events AS later_turn
              WHERE later_turn.session_id = finished.session_id
                AND later_turn.actor_id = finished.actor_id
                AND later_turn.event_type = 'turn.started'
                AND later_turn.cursor > (
                    SELECT MAX(turn_end.cursor)
                    FROM canonical_events AS turn_end
                    WHERE turn_end.session_id = finished.session_id
                      AND turn_end.actor_id = finished.actor_id
                      AND turn_end.event_type IN ('turn.finished', 'turn.aborted')
                )
                AND later_turn.cursor < finished.cursor
          )
          AND NOT EXISTS (
              SELECT 1
              FROM canonical_events AS settled
              WHERE settled.event_id =
                  'migration:9:shell-settled:' || finished.event_id
          )
        """,
    ),
    10: (
        """
        CREATE TABLE IF NOT EXISTS pending_raw_events(
            raw_event_row_id INTEGER PRIMARY KEY,
            raw_event_id TEXT NOT NULL UNIQUE,
            FOREIGN KEY(raw_event_id) REFERENCES raw_events(raw_event_id)
                ON DELETE CASCADE
        )
        """,
        """
        INSERT OR IGNORE INTO pending_raw_events(raw_event_row_id, raw_event_id)
        SELECT raw_events.id, raw_events.raw_event_id
        FROM raw_events
        LEFT JOIN interpretations USING(raw_event_id)
        WHERE interpretations.raw_event_id IS NULL
        ORDER BY raw_events.id
        """,
    ),
    11: (
        """
        ALTER TABLE raw_events
        ADD COLUMN payload_codec TEXT NOT NULL DEFAULT 'identity'
            CHECK(payload_codec IN ('identity', 'zlib'))
        """,
    ),
    12: (
        """
        ALTER TABLE sessions
        ADD COLUMN lifecycle TEXT NOT NULL DEFAULT 'running'
            CHECK(lifecycle IN ('running', 'finished'))
        """,
        """
        UPDATE sessions
        SET lifecycle = COALESCE((
            SELECT CASE canonical_events.event_type
                WHEN 'session.finished' THEN 'finished'
                ELSE 'running'
            END
            FROM canonical_events
            WHERE canonical_events.session_id = sessions.session_id
              AND canonical_events.event_type IN ('session.started', 'session.finished')
            ORDER BY canonical_events.cursor DESC
            LIMIT 1
        ), 'running')
        """,
        """
        CREATE TRIGGER sessions_lifecycle_after_event
        AFTER INSERT ON canonical_events
        WHEN NEW.event_type IN ('session.started', 'session.finished')
        BEGIN
            UPDATE sessions
            SET lifecycle = CASE NEW.event_type
                WHEN 'session.finished' THEN 'finished'
                ELSE 'running'
            END
            WHERE session_id = NEW.session_id;
        END
        """,
        """
        CREATE TRIGGER sessions_lifecycle_after_insert
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
        END
        """,
    ),
    13: (
        """
        ALTER TABLE sessions
        ADD COLUMN project_directory TEXT
        """,
    ),
    14: (
        """
        CREATE TABLE IF NOT EXISTS naming_jobs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_key TEXT NOT NULL UNIQUE,
            session_id TEXT NOT NULL,
            prompt TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('pending', 'running', 'completed', 'failed')),
            title TEXT,
            error TEXT
        )
        """,
    ),
    15: (
        """
        WITH duplicate_completions AS (
            SELECT
                backgrounded.event_id AS backgrounded_event_id,
                backgrounded.schema_version AS schema_version,
                backgrounded.session_id AS session_id,
                backgrounded.actor_id AS actor_id,
                backgrounded.turn_id AS turn_id,
                backgrounded.parent_actor_id AS parent_actor_id,
                backgrounded.harness AS harness,
                replacement_finished.occurred_at AS occurred_at,
                replacement_finished.terminal_window_id AS terminal_window_id,
                replacement_finished.harness_process_id AS harness_process_id,
                replacement_finished.accepted_at AS accepted_at,
                json_extract(backgrounded.payload, '$.shell_id') AS shell_id,
                json_extract(replacement_finished.payload, '$.outcome') AS outcome,
                ROW_NUMBER() OVER (
                    PARTITION BY backgrounded.event_id
                    ORDER BY replacement_started.cursor
                ) AS candidate_order
            FROM session_data_actors AS actor_state
            JOIN json_each(
                actor_state.payload,
                '$.background.running_shell_ids'
            ) AS running_shell
            JOIN canonical_events AS backgrounded
              ON backgrounded.session_id = actor_state.session_id
             AND backgrounded.actor_id = actor_state.actor_id
             AND backgrounded.event_type = 'shell.backgrounded'
             AND json_extract(backgrounded.payload, '$.shell_id') =
                 running_shell.value
            JOIN canonical_events AS original_started
              ON original_started.session_id = backgrounded.session_id
             AND original_started.actor_id = backgrounded.actor_id
             AND original_started.event_type = 'shell.started'
             AND json_extract(original_started.payload, '$.shell_id') =
                 json_extract(backgrounded.payload, '$.shell_id')
             AND original_started.cursor < backgrounded.cursor
            JOIN canonical_events AS replacement_started
              ON replacement_started.session_id = backgrounded.session_id
             AND replacement_started.actor_id = backgrounded.actor_id
             AND replacement_started.event_type = 'shell.started'
             AND replacement_started.cursor > original_started.cursor
             AND json_extract(replacement_started.payload, '$.shell_id') !=
                 json_extract(backgrounded.payload, '$.shell_id')
             AND json_extract(replacement_started.payload, '$.command.text') =
                 json_extract(original_started.payload, '$.command.text')
            JOIN interpretation_events AS replacement_started_source
              ON replacement_started_source.event_id = replacement_started.event_id
             AND replacement_started_source.storage_result = 'accepted'
            JOIN canonical_events AS replacement_finished
              ON replacement_finished.session_id = replacement_started.session_id
             AND replacement_finished.actor_id = replacement_started.actor_id
             AND replacement_finished.event_type = 'shell.finished'
             AND json_extract(replacement_finished.payload, '$.shell_id') =
                 json_extract(replacement_started.payload, '$.shell_id')
            JOIN interpretation_events AS replacement_finished_source
              ON replacement_finished_source.event_id = replacement_finished.event_id
             AND replacement_finished_source.raw_event_id =
                 replacement_started_source.raw_event_id
             AND replacement_finished_source.event_order >
                 replacement_started_source.event_order
             AND replacement_finished_source.storage_result = 'accepted'
            WHERE backgrounded.harness = 'codex'
              AND NOT EXISTS (
                  SELECT 1
                  FROM canonical_events AS original_closed
                  WHERE original_closed.session_id = backgrounded.session_id
                    AND original_closed.actor_id = backgrounded.actor_id
                    AND original_closed.event_type = 'shell.output_finished'
                    AND json_extract(original_closed.payload, '$.shell_id') =
                        json_extract(backgrounded.payload, '$.shell_id')
              )
        )
        INSERT INTO canonical_events(
            event_id, schema_version, event_type, session_id, actor_id,
            turn_id, parent_actor_id, harness, occurred_at,
            terminal_window_id, harness_process_id, accepted_at, payload
        )
        SELECT
            'migration:15:recovered-shell-output-finished:' ||
                backgrounded_event_id,
            schema_version,
            'shell.output_finished',
            session_id,
            actor_id,
            turn_id,
            parent_actor_id,
            harness,
            occurred_at,
            terminal_window_id,
            harness_process_id,
            accepted_at,
            json_object('shell_id', shell_id, 'outcome', outcome)
        FROM duplicate_completions
        WHERE candidate_order = 1
        """,
    ),
    16: (
        """
        INSERT INTO canonical_events(
            event_id, schema_version, event_type, session_id, actor_id,
            turn_id, parent_actor_id, harness, occurred_at,
            terminal_window_id, harness_process_id, accepted_at, payload
        )
        SELECT
            'migration:16:session-run-finished:' || exit.raw_event_id,
            previous_finish.schema_version,
            'session.finished',
            exit.session_id,
            exit.actor_id,
            NULL,
            exit.parent_actor_id,
            exit.harness,
            NULL,
            exit.terminal_window_id,
            run_started.harness_process_id,
            interpretation.completed_at,
            previous_finish.payload
        FROM raw_events AS exit
        JOIN interpretations AS interpretation
          ON interpretation.raw_event_id = exit.raw_event_id
        JOIN interpretation_events AS verdict
          ON verdict.raw_event_id = exit.raw_event_id
         AND verdict.storage_result = 'deduplicated'
        JOIN canonical_events AS previous_finish
          ON previous_finish.event_id = verdict.event_id
         AND previous_finish.event_type = 'session.finished'
        JOIN canonical_events AS run_started
          ON run_started.session_id = exit.session_id
         AND run_started.event_type = 'session.started'
         AND run_started.terminal_window_id = exit.terminal_window_id
         AND run_started.cursor > previous_finish.cursor
        WHERE exit.source_type = 'liveness'
          AND exit.terminal_window_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM canonical_events AS later_lifecycle
              WHERE later_lifecycle.session_id = exit.session_id
                AND later_lifecycle.event_type IN (
                    'session.started', 'session.finished'
                )
                AND later_lifecycle.cursor > run_started.cursor
          )
        """,
    ),
    17: (
        "DROP INDEX index_session_entries_session",
        "CREATE INDEX index_session_entries_session ON session_entries(session_id, cursor, occurred_at)",
    ),
    18: (
        """
        INSERT OR IGNORE INTO pending_raw_events(raw_event_row_id, raw_event_id)
        SELECT raw.id, raw.raw_event_id
        FROM raw_events AS raw
        JOIN interpretations AS interpretation
          ON interpretation.raw_event_id = raw.raw_event_id
        WHERE raw.harness = 'claude_code'
          AND raw.source_type = 'hook'
          AND raw.source_name = 'PostToolUse'
          AND interpretation.decision = 'ignored_nonsemantic'
        """,
        """
        DELETE FROM interpretation_events
        WHERE raw_event_id IN (
            SELECT raw.raw_event_id
            FROM raw_events AS raw
            JOIN interpretations AS interpretation
              ON interpretation.raw_event_id = raw.raw_event_id
            WHERE raw.harness = 'claude_code'
              AND raw.source_type = 'hook'
              AND raw.source_name = 'PostToolUse'
              AND interpretation.decision = 'ignored_nonsemantic'
        )
        """,
        """
        DELETE FROM interpretations
        WHERE raw_event_id IN (
            SELECT raw.raw_event_id
            FROM raw_events AS raw
            WHERE raw.harness = 'claude_code'
              AND raw.source_type = 'hook'
              AND raw.source_name = 'PostToolUse'
              AND raw.raw_event_id IN (
                  SELECT pending.raw_event_id
                  FROM pending_raw_events AS pending
              )
        )
          AND decision = 'ignored_nonsemantic'
        """,
    ),
    19: (
        # ToolSearch/WebSearch hook results used to be stored as the hook's raw
        # response object. The current translator renders readable text, but
        # the canonical event id is stable, so simply retrying would deduplicate
        # against the bad event. Remember only repairable events (those with a
        # self-contained PostToolUse hook), remove their derived facts, and
        # requeue that hook. Raw observations remain append-only.
        """
        CREATE TEMP TABLE tool_result_repairs(
            event_id TEXT PRIMARY KEY,
            raw_event_id TEXT NOT NULL UNIQUE,
            raw_event_row_id INTEGER NOT NULL
        )
        """,
        """
        INSERT INTO tool_result_repairs(event_id, raw_event_id, raw_event_row_id)
        SELECT canonical.event_id, raw.raw_event_id, raw.id
        FROM canonical_events AS canonical
        JOIN interpretation_events AS verdict
          ON verdict.event_id = canonical.event_id
        JOIN raw_events AS raw
          ON raw.raw_event_id = verdict.raw_event_id
        WHERE canonical.event_type = 'search.performed'
          AND canonical.harness = 'claude_code'
          AND json_extract(canonical.payload, '$.tool') IN (
              'ToolSearch', 'WebSearch'
          )
          AND json_type(canonical.payload, '$.result.json_text') = 'text'
          AND raw.source_type = 'hook'
          AND raw.source_name = 'PostToolUse'
        GROUP BY canonical.event_id
        """,
        """
        INSERT OR IGNORE INTO pending_raw_events(raw_event_row_id, raw_event_id)
        SELECT raw_event_row_id, raw_event_id FROM tool_result_repairs
        """,
        """
        DELETE FROM interpretation_events
        WHERE event_id IN (SELECT event_id FROM tool_result_repairs)
        """,
        """
        DELETE FROM interpretations
        WHERE raw_event_id IN (
            SELECT raw_event_id FROM tool_result_repairs
        )
        """,
        """
        DELETE FROM canonical_events
        WHERE event_id IN (SELECT event_id FROM tool_result_repairs)
        """,
        # Session data is a disposable projection. Clearing it avoids keeping
        # the old expandable card under the same entry id; the reaction loop
        # rebuilds every row from the canonical log, including repaired events.
        "DELETE FROM session_entries WHERE EXISTS (SELECT 1 FROM tool_result_repairs)",
        "DELETE FROM session_data_actors WHERE EXISTS (SELECT 1 FROM tool_result_repairs)",
        "DELETE FROM session_data WHERE EXISTS (SELECT 1 FROM tool_result_repairs)",
        "DELETE FROM reaction_progress WHERE EXISTS (SELECT 1 FROM tool_result_repairs)",
        "DELETE FROM sqlite_sequence WHERE name='session_entries' AND EXISTS (SELECT 1 FROM tool_result_repairs)",
        "DROP TABLE tool_result_repairs",
    ),
    20: (
        # Version 5 normalized the actor projection's ModelReference, but the
        # canonical log intentionally remained untouched at the time. A later
        # full projection rebuild reads that durable log through today's closed
        # payload models, so normalize the three canonical event fields that
        # carried the same legacy `{native_id, selection_id}` shape.
        """
        UPDATE canonical_events
        SET payload = json_set(
            json_remove(
                payload,
                '$.current.native_id',
                '$.current.selection_id'
            ),
            '$.current.name', json_extract(payload, '$.current.native_id')
        )
        WHERE event_type = 'model.changed'
          AND json_type(payload, '$.current') = 'object'
          AND json_type(payload, '$.current.name') IS NULL
          AND json_type(payload, '$.current.native_id') = 'text'
        """,
        """
        UPDATE canonical_events
        SET payload = json_set(
            json_remove(
                payload,
                '$.previous.native_id',
                '$.previous.selection_id'
            ),
            '$.previous.name', json_extract(payload, '$.previous.native_id')
        )
        WHERE event_type = 'model.changed'
          AND json_type(payload, '$.previous') = 'object'
          AND json_type(payload, '$.previous.name') IS NULL
          AND json_type(payload, '$.previous.native_id') = 'text'
        """,
        """
        UPDATE canonical_events
        SET payload = json_set(
            json_remove(payload, '$.model.native_id', '$.model.selection_id'),
            '$.model.name', json_extract(payload, '$.model.native_id')
        )
        WHERE event_type IN ('context.reported', 'usage.reported')
          AND json_type(payload, '$.model') = 'object'
          AND json_type(payload, '$.model.name') IS NULL
          AND json_type(payload, '$.model.native_id') = 'text'
        """,
    ),
    23: (
        "ALTER TABLE shell_output RENAME TO shell_output_one_file",
        """
        CREATE TABLE shell_output(
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
        )
        """,
        "INSERT INTO shell_output SELECT * FROM shell_output_one_file",
        "DROP TABLE shell_output_one_file",
    ),
    24: (
        """
        UPDATE session_data_actors
        SET payload = json_set(
            payload,
            '$.statistics.tool_counts',
            json((
                SELECT json_group_array(
                    json_object(
                        'tool', json_extract(value, '$[0]'),
                        'count', json_extract(value, '$[1]')
                    )
                )
                FROM json_each(payload, '$.statistics.tool_counts')
            ))
        )
        WHERE json_type(payload, '$.statistics.tool_counts') = 'array'
          AND json_type(payload, '$.statistics.tool_counts[0]') = 'array'
        """,
    ),
    25: (
        """
        CREATE TABLE IF NOT EXISTS goal_dismissals(
            session_id TEXT PRIMARY KEY,
            objective TEXT NOT NULL,
            dismissed_cursor INTEGER NOT NULL
        )
        """,
    ),
    27: _EXTENSION_CATALOG_TABLES,
    28: _EXTENSION_LIFECYCLE_TABLES,
    29: _EXTENSION_RESOLUTION_TABLES,
    30: _SCOPED_RAW_MIGRATION,
    31: _VERSIONED_CANONICAL_MIGRATION,
    32: _INTERPRETATION_JOURNAL_MIGRATION_V32,
    33: _EXTENSION_SOURCE_TABLES,
    35: _NORMALIZED_JOURNAL_MIGRATION,
    36: _SESSION_ENTRY_COMMIT_MIGRATION,
})

_SCHEMA_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_version(
    id INTEGER PRIMARY KEY CHECK(id = 1),
    version INTEGER NOT NULL,
    applied_at REAL NOT NULL
);
"""


_MAIN_SCHEMA_BODY = """
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
-- by the writers behind `SessionDataRepository`. `revision` and
-- `session_entries.commit_cursor` use the canonical event cursor, so "everything
-- after cursor C" is one question with one answer across both kinds of change.
-- One commit can add several entries; the row `cursor` orders them for paging
-- and `position` orders them inside their commit.

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
    commit_cursor INTEGER NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
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

CREATE INDEX IF NOT EXISTS index_session_entries_commit
    ON session_entries(commit_cursor, position);

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
"""

MAIN_SCHEMA = "".join((
    _SCHEMA_VERSION_TABLE, _MAIN_SCHEMA_BODY,
    ";\n".join((
        _RAW_EVENT_TABLE, *_RAW_EVENT_INDEXES, _PENDING_RAW_TABLE,
        _CANONICAL_HISTORY_TABLE, _DEFAULT_CANONICAL_HISTORY, _CANONICAL_EVENT_TABLE, *_CANONICAL_EVENT_INDEXES,
        _VERSIONED_INTERPRETATION_TABLE, _VERSIONED_INTERPRETATION_LINK_TABLE,
        *_CURRENT_HISTORY_VIEWS, *_CURRENT_LIFECYCLE_TRIGGERS,
        *_INTERPRETATION_JOURNAL_TABLES,
        *_EXTENSION_CATALOG_TABLES, *_EXTENSION_LIFECYCLE_TABLES, *_EXTENSION_RESOLUTION_TABLES,
        *_EXTENSION_SOURCE_TABLES, *_EXTENSION_RECORD_TABLES,
        *_EXTENSION_SHUTDOWN_TABLES, *_EXTENSION_JOB_TABLES, *_EXTENSION_OBSERVER_CURSOR_TABLES,
        *_CANONICAL_SCOPE_HEAD_TABLES, _EXTENSION_JOB_STATE_INDEX, *_EXTENSION_PROJECTION_GENERATION_TABLES,
        *_EXTENSION_HEALTH_TABLES,
    )), ";\n",
))


_AUDIT_SCHEMA_BODY = """
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

AUDIT_SCHEMA = _SCHEMA_VERSION_TABLE + _AUDIT_SCHEMA_BODY
