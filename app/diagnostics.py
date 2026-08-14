"""Typed reads from the operational diagnostic database."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from domain.ids import SessionId


@dataclass(frozen=True)
class ApplicationError:
    error_id: int
    timestamp: float
    component: str
    action: str
    traceback: str
    context: str


class OperationalDiagnostics:
    """Read product failures; these records are not canonical harness facts."""

    def __init__(self, database_path: str) -> None:
        self.database_path = os.path.abspath(database_path)

    def errors(self, session_id: SessionId) -> tuple[ApplicationError, ...]:
        if not os.path.isfile(self.database_path):
            return ()
        connection = sqlite3.connect(
            f"file:{self.database_path}?mode=ro",
            uri=True,
            timeout=10,
        )
        try:
            rows = connection.execute(
                "SELECT id, ts, script, func, traceback, context FROM errors "
                "WHERE session_id=? ORDER BY id",
                (str(session_id),),
            ).fetchall()
        finally:
            connection.close()
        return tuple(
            ApplicationError(
                error_id=int(error_id),
                timestamp=float(timestamp),
                component=component or "",
                action=action or "",
                traceback=traceback or "",
                context=context or "",
            )
            for error_id, timestamp, component, action, traceback, context in rows
        )

    def error_counts(self) -> Mapping[SessionId, int]:
        if not os.path.isfile(self.database_path):
            return MappingProxyType({})
        connection = sqlite3.connect(
            f"file:{self.database_path}?mode=ro",
            uri=True,
            timeout=10,
        )
        try:
            rows = connection.execute(
                "SELECT session_id, COUNT(*) FROM errors "
                "WHERE session_id != '' GROUP BY session_id"
            ).fetchall()
        finally:
            connection.close()
        return MappingProxyType(
            {SessionId(session_id): int(count) for session_id, count in rows}
        )
