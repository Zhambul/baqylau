"""A session's unsent work, across four tables.

Every write is one transaction. The draft's sequence guard in particular MUST
be: the daemon serves requests on many threads, each with its own connection, so
a get-then-set would let a second, older write clobber a newer one.
"""

from __future__ import annotations

import sqlite3

from domain.ids import SessionId
from domain.workspace import ComposerDraft, ComposerQueue, DialogDraft, SessionWorkspace
from repository.contract.workspace import SessionWorkspaceRepository
from repository.impl.sqlite import rows
from repository.impl.sqlite.connection import SqliteDatabase
from repository.mapper import workspace as mapper


class SqliteSessionWorkspaceRepository(SessionWorkspaceRepository):
    def __init__(self, sqlite_database: SqliteDatabase) -> None:
        self.sqlite_database = sqlite_database

    def find(self, session_id: SessionId) -> SessionWorkspace | None:
        with self.sqlite_database.read() as connection:
            row = connection.execute(
                "SELECT * FROM session_workspaces WHERE session_id=?", (str(session_id),)
            ).fetchone()
            if row is None:
                return None
            queue_items = connection.execute(
                "SELECT * FROM composer_queue_items WHERE session_id=? ORDER BY position",
                (str(session_id),),
            ).fetchall()
            answers = connection.execute(
                "SELECT * FROM dialog_answers WHERE session_id=? ORDER BY prompt_index",
                (str(session_id),),
            ).fetchall()
            selections = connection.execute(
                "SELECT * FROM dialog_answer_selections WHERE session_id=? "
                "ORDER BY prompt_index, selection_index",
                (str(session_id),),
            ).fetchall()
        return mapper.session_workspace(
            rows.session_workspace(row),
            tuple(rows.composer_queue_item(item) for item in queue_items),
            tuple(rows.dialog_answer(answer) for answer in answers),
            tuple(rows.dialog_answer_selection(selection) for selection in selections),
        )

    def save_composer_draft(self, session_id: SessionId, composer_draft: ComposerDraft) -> bool:
        with self.sqlite_database.write() as connection:
            self._ensure(connection, session_id)
            current = connection.execute(
                "SELECT composer_sequence FROM session_workspaces WHERE session_id=?",
                (str(session_id),),
            ).fetchone()
            if composer_draft.sequence < current["composer_sequence"]:
                return False
            connection.execute(
                "UPDATE session_workspaces SET composer_text=?, composer_origin=?, "
                "composer_sequence=? WHERE session_id=?",
                (
                    composer_draft.text if composer_draft.text.strip() else "",
                    composer_draft.origin,
                    composer_draft.sequence,
                    str(session_id),
                ),
            )
        return True

    def save_composer_queue(self, session_id: SessionId, composer_queue: ComposerQueue) -> None:
        with self.sqlite_database.write() as connection:
            self._ensure(connection, session_id)
            connection.execute(
                "UPDATE session_workspaces SET queue_origin=? WHERE session_id=?",
                (composer_queue.origin, str(session_id)),
            )
            connection.execute(
                "DELETE FROM composer_queue_items WHERE session_id=?", (str(session_id),)
            )
            connection.executemany(
                "INSERT INTO composer_queue_items(session_id, position, text) VALUES(?, ?, ?)",
                mapper.queue_item_values(session_id, composer_queue),
            )

    def save_dialog_draft(self, session_id: SessionId, dialog_draft: DialogDraft) -> None:
        with self.sqlite_database.write() as connection:
            self._ensure(connection, session_id)
            connection.execute(
                "UPDATE session_workspaces SET dialog_attention_id=?, dialog_origin=? "
                "WHERE session_id=?",
                (str(dialog_draft.attention_id), dialog_draft.origin, str(session_id)),
            )
            connection.execute(
                "DELETE FROM dialog_answers WHERE session_id=?", (str(session_id),)
            )
            connection.execute(
                "DELETE FROM dialog_answer_selections WHERE session_id=?", (str(session_id),)
            )
            connection.executemany(
                "INSERT INTO dialog_answers(session_id, prompt_index, other_text) "
                "VALUES(?, ?, ?)",
                mapper.dialog_answer_values(session_id, dialog_draft),
            )
            connection.executemany(
                "INSERT INTO dialog_answer_selections("
                "session_id, prompt_index, selection_index, selected_value) VALUES(?, ?, ?, ?)",
                mapper.dialog_selection_values(session_id, dialog_draft),
            )

    @staticmethod
    def _ensure(connection: sqlite3.Connection, session_id: SessionId) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO session_workspaces(session_id) VALUES(?)",
            (str(session_id),),
        )
