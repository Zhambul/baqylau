"""Every table we own, in one file, with one version per database.

Two databases, and the reason each is its own file is in `core/data.py`.

The write split inside `main.db` is the design: `sessions` is written only by
the interpreter's session-upsert reaction (birth and upkeep derive from
committed facts), `raw_events` by any recorder, and the interpretation tables
only by the interpreter. Positions are not stored anywhere separate: a pulled
source resumes from the `source_position` of the last raw event carrying its
`source_identity`, so recorded progress can never drift from the raw events.

There is no key–value table. Nine preference entities that used to be JSON
blobs under nine keys have nine tables with real primary keys; the queue, the
dialog answers and the usage windows are rows rather than encoded lists. Six
opaque columns remain and each is deliberate: `canonical_events.payload` is the
canonical fact body, closed and versioned by `repository/mapper/facts.py`;
`raw_events.payload` restores to the verbatim bytes we observed, which is the
whole point of keeping it; `state_files.content` is a free-form audit blob written by a
facade whose contract is "record anything, never raise"; and the three read-model
payloads (`session_data`, `session_data_actors`, `session_entries`) are closed
typed documents of `domain/sessiondata.py` and `domain/entries.py`, validated on
the way in and out the same way — a column per field would be a hundred
columns, half of them null, and none of them queried.
"""

from __future__ import annotations

MAIN_SCHEMA_VERSION = 16
AUDIT_SCHEMA_VERSION = 1

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
MAIN_MIGRATIONS: dict[int, tuple[str, ...]] = {
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
             AND replacement_started.cursor > backgrounded.cursor
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
                    AND original_closed.event_type IN (
                        'shell.finished', 'shell.output_finished'
                    )
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
}


_SCHEMA_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_version(
    id INTEGER PRIMARY KEY CHECK(id = 1),
    version INTEGER NOT NULL,
    applied_at REAL NOT NULL
);
"""


MAIN_SCHEMA = _SCHEMA_VERSION_TABLE + """
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
    PRIMARY KEY(session_id, shell_id)
);

-- === the read model ========================================================
--
-- What every frontend reads, and the only thing they read. Written at push time
-- by the writers behind `SessionDataRepository`; `revision` and
-- `session_entries.cursor` come from ONE counter, so "everything after cursor
-- C" is a single question with a single answer across both kinds of change.

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
    ON session_entries(session_id, cursor);

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
