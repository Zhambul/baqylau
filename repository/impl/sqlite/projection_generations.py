# Copyright (c) 2026 Zhambyl Yermagambet
"""Store candidate projection generations beside the live generation, and switch them atomically."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

from core.work_queue import WorkKind
from repository.contract.projection_generations import (
    GenerationComparison,
    GenerationState,
    ProjectionGeneration,
    ProjectionSwitchError,
)
from repository.impl.sqlite import connection, generation_heads, generation_mirrors

if TYPE_CHECKING:
    import sqlite3

_GENERATION_SQL = "SELECT * FROM extension_projection_generations WHERE generation=?"


@dataclass(frozen=True)
class SqliteProjectionGenerationRepository:
    """Create, read, compare, and switch projection generations."""

    database: connection.SqliteDatabase

    def active_generation(self, owner: str) -> str:
        """Read one owner's active projection generation.

        Returns:
            The head generation, or the default before the first switch.

        """
        with self.database.read() as connection_handle:
            return generation_heads.active_generation(connection_handle, owner)

    def create(self, owner: str, history_revision: str) -> ProjectionGeneration:
        """Start one empty candidate generation for an owner.

        Returns:
            The new building generation.

        """
        now = time.time()
        generation = f"generation-{uuid4().hex}"
        with self.database.write(WorkKind.CANONICAL, notify_readers=False) as connection_handle:
            connection_handle.execute(
                "INSERT INTO extension_projection_generations(generation, owner, history_revision, state, created_at, "
                "updated_at) VALUES(?, ?, ?, 'building', ?, ?)",
                (generation, owner, history_revision, now, now),
            )
            return _generation(connection_handle, generation)

    def read(self, generation: str) -> ProjectionGeneration | None:
        """Read one stored generation.

        Returns:
            The generation, or None when it does not exist.

        """
        with self.database.read() as connection_handle:
            row = connection_handle.execute(_GENERATION_SQL, (generation,)).fetchone()
        return None if row is None else _row_generation(row)

    def generations_in_state(self, generation_state: GenerationState) -> tuple[ProjectionGeneration, ...]:
        """Read the generations in one state, oldest first.

        Returns:
            The matching generations.

        """
        with self.database.read() as connection_handle:
            rows = connection_handle.execute(
                "SELECT * FROM extension_projection_generations WHERE state=? ORDER BY created_at", (generation_state,),
            ).fetchall()
        return tuple(_row_generation(row) for row in rows)

    def settle(self, generation: str, generation_state: GenerationState, diagnostic: str | None = None) -> None:
        """Record a built or failed candidate and its comparison with the live generation."""
        with self.database.write(notify_readers=False) as connection_handle:
            owner = _generation(connection_handle, generation).owner
            comparison = generation_mirrors.compare(connection_handle, owner, generation).model_dump_json()
            connection_handle.execute(
                "UPDATE extension_projection_generations SET state=?, comparison=?, diagnostic=?, updated_at=? "
                "WHERE generation=? AND state='building'",
                (generation_state, comparison, diagnostic, time.time(), generation),
            )

    def resume(self, generation: str) -> ProjectionGeneration:
        """Let a retired generation catch up from its own cursors before a switch back to it.

        Returns:
            The generation, building again.

        Raises:
            ProjectionSwitchError: If the generation is not retired.

        """
        with self.database.write(WorkKind.CANONICAL, notify_readers=False) as connection_handle:
            if _generation(connection_handle, generation).state != GenerationState.RETIRED:
                message = "only a retired projection generation can catch up again"
                raise ProjectionSwitchError(message)
            connection_handle.execute(
                "UPDATE extension_projection_generations SET state='building', comparison=NULL, updated_at=? "
                "WHERE generation=?",
                (time.time(), generation),
            )
            return _generation(connection_handle, generation)

    def switch(self, generation: str) -> ProjectionGeneration:
        """Make a ready or retired generation live, and keep the previous live one for recovery.

        Returns:
            The generation after the switch.

        Raises:
            ProjectionSwitchError: If the generation is not ready or retired, or is behind the live one.

        """
        with self.database.write() as connection_handle:
            target = _generation(connection_handle, generation)
            if target.state not in {GenerationState.READY, GenerationState.RETIRED}:
                message = "only a ready or retired projection generation can become live"
                raise ProjectionSwitchError(message)
            previous = generation_mirrors.ensure_generation(connection_handle, target.owner, target.history_revision)
            generation_mirrors.require_caught_up(connection_handle, target.owner, (generation, previous))
            generation_mirrors.make_live(connection_handle, target.owner, (generation, previous))
            return _generation(connection_handle, generation)


def _generation(connection_handle: sqlite3.Connection, generation: str) -> ProjectionGeneration:
    row = connection_handle.execute(_GENERATION_SQL, (generation,)).fetchone()
    if row is None:
        message = "projection generation does not exist"
        raise ProjectionSwitchError(message)
    return _row_generation(row)


def _row_generation(row: sqlite3.Row) -> ProjectionGeneration:
    comparison = row["comparison"]
    return ProjectionGeneration(
        generation=row["generation"],
        owner=row["owner"],
        history_revision=row["history_revision"],
        state=GenerationState(row["state"]),
        comparison=None if comparison is None else GenerationComparison.model_validate_json(comparison),
        diagnostic=row["diagnostic"],
    )
