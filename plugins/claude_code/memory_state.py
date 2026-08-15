"""Claude Code memory state and post-commit hook capture."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass

from contracts.harness import (
    HarnessMemorySnapshot,
    HookAction,
    MemoryNoteRecord,
    MemorySearchHit,
    MemorySearchRecord,
)
from domain.ids import SessionId
from plugins.claude_code import application_data, memcmd, memory

ACTION_NAMES = {
    "Read": "Read",
    "Write": "Write",
    "Edit": "Update",
    "MultiEdit": "Update",
    "NotebookEdit": "Update",
}
ACTION_RANK = {"Read": 1, "Update": 2, "Write": 3}


def _database_path() -> str:
    return application_data.path("memory.db")


def _connect() -> sqlite3.Connection:
    path = _database_path()
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS memory_notes(
            session_id TEXT NOT NULL,
            path TEXT NOT NULL,
            name TEXT NOT NULL,
            action TEXT NOT NULL,
            actor_name TEXT,
            access_count INTEGER NOT NULL,
            accessed_at REAL NOT NULL,
            PRIMARY KEY(session_id, path)
        );
        CREATE TABLE IF NOT EXISTS memory_searches(
            session_id TEXT NOT NULL,
            command_name TEXT NOT NULL,
            command_action TEXT NOT NULL,
            query TEXT NOT NULL,
            command TEXT NOT NULL,
            expanded_queries TEXT NOT NULL,
            hits TEXT NOT NULL,
            actor_name TEXT,
            search_count INTEGER NOT NULL,
            searched_at REAL NOT NULL,
            PRIMARY KEY(session_id, command_name, command_action, query)
        );
        """
    )
    return connection


@dataclass(frozen=True)
class CaptureMemory(HookAction):
    document: dict

    def start(self) -> None:
        working_directory = str(self.document.get("cwd") or "")
        if not memory.in_scope(working_directory):
            return
        tool_name = str(self.document.get("tool_name") or "")
        tool_input = self.document.get("tool_input") or {}
        if not isinstance(tool_input, dict):
            return
        if tool_name in ACTION_NAMES:
            path = str(
                tool_input.get("file_path") or tool_input.get("notebook_path") or ""
            )
            if path and memory.is_memory(path):
                _record_note(
                    str(self.document["session_id"]),
                    path,
                    ACTION_NAMES[tool_name],
                    self.document.get("agent_id"),
                )
            return
        if tool_name == "Bash":
            command = str(tool_input.get("command") or "")
            response = self.document.get("tool_response") or {}
            output = (
                str(response.get("stdout") or "")
                + (("\n" + str(response.get("stderr"))) if response.get("stderr") else "")
                if isinstance(response, dict)
                else str(response)
            ).rstrip("\n")
            _record_command(
                str(self.document["session_id"]),
                command,
                working_directory,
                output,
                self.document.get("agent_id"),
            )


def _record_note(session_id: str, path: str, action: str, actor_name) -> None:
    now = time.time()
    with closing(_connect()) as connection, connection:
        row = connection.execute(
            "SELECT action, access_count FROM memory_notes WHERE session_id=? AND path=?",
            (session_id, path),
        ).fetchone()
        selected_action = action
        count = 1
        if row is not None:
            count = int(row["access_count"]) + 1
            if ACTION_RANK.get(row["action"], 0) > ACTION_RANK.get(action, 0):
                selected_action = row["action"]
        connection.execute(
            "INSERT OR REPLACE INTO memory_notes VALUES(?,?,?,?,?,?,?)",
            (
                session_id,
                path,
                os.path.basename(path.rstrip("/")) or path,
                selected_action,
                str(actor_name) if actor_name else None,
                count,
                now,
            ),
        )


def _record_command(
    session_id: str,
    command: str,
    working_directory: str,
    output: str,
    actor_name,
) -> None:
    notes, searches = memcmd.plan(command, working_directory)
    for path in notes:
        _record_note(session_id, path, "Read", actor_name)
    hits, expanded_queries = ((), ())
    if len(searches) == 1:
        hits, expanded_queries = memcmd.qmd_hits(output)
    now = time.time()
    with closing(_connect()) as connection, connection:
        for command_name, command_action, query in searches:
            row = connection.execute(
                "SELECT search_count FROM memory_searches WHERE session_id=? "
                "AND command_name=? AND command_action=? AND query=?",
                (session_id, command_name, command_action, query),
            ).fetchone()
            connection.execute(
                "INSERT OR REPLACE INTO memory_searches VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    session_id,
                    command_name,
                    command_action,
                    query,
                    command,
                    json.dumps(list(expanded_queries), ensure_ascii=False),
                    json.dumps(list(hits), ensure_ascii=False),
                    str(actor_name) if actor_name else None,
                    (int(row["search_count"]) + 1) if row is not None else 1,
                    now,
                ),
            )


def snapshot(session_id: SessionId) -> HarnessMemorySnapshot:
    with closing(_connect()) as connection, connection:
        note_rows = connection.execute(
            "SELECT * FROM memory_notes WHERE session_id=? ORDER BY accessed_at DESC",
            (str(session_id),),
        ).fetchall()
        search_rows = connection.execute(
            "SELECT * FROM memory_searches WHERE session_id=? ORDER BY searched_at DESC",
            (str(session_id),),
        ).fetchall()
    notes = tuple(
        MemoryNoteRecord(
            path=row["path"],
            relative_path=memory.rel(row["path"]),
            name=row["name"],
            action=row["action"],
            actor_name=row["actor_name"],
            access_count=row["access_count"],
            accessed_at=row["accessed_at"],
        )
        for row in note_rows
    )
    searches = tuple(_search_record(row) for row in search_rows)
    return HarnessMemorySnapshot(notes, searches)


def item_count(session_id: SessionId) -> int:
    with closing(_connect()) as connection, connection:
        note_count = connection.execute(
            "SELECT count(*) FROM memory_notes WHERE session_id=?",
            (str(session_id),),
        ).fetchone()[0]
        search_count = connection.execute(
            "SELECT count(*) FROM memory_searches WHERE session_id=?",
            (str(session_id),),
        ).fetchone()[0]
    return int(note_count) + int(search_count)


def _search_record(row: sqlite3.Row) -> MemorySearchRecord:
    hits = tuple(
        MemorySearchHit(
            path=str(hit.get("path") or ""),
            relative_path=str(hit.get("rel") or ""),
            name=str(hit.get("name") or ""),
            line_number=int(hit["line"]) if hit.get("line") is not None else None,
            title=str(hit.get("title") or ""),
            score=str(hit.get("score") or ""),
            snippet=str(hit.get("snippet") or ""),
        )
        for hit in json.loads(row["hits"])
        if isinstance(hit, dict)
    )
    return MemorySearchRecord(
        command_name=row["command_name"],
        command_action=row["command_action"],
        query=row["query"],
        command=row["command"],
        expanded_queries=tuple(json.loads(row["expanded_queries"])),
        hits=hits,
        actor_name=row["actor_name"],
        search_count=row["search_count"],
        searched_at=row["searched_at"],
    )
