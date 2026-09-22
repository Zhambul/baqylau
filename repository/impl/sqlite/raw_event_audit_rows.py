# Copyright (c) 2026 Zhambyl Yermagambet
"""Map stored audit rows to bounded step metadata."""

import sqlite3
from dataclasses import dataclass

from domain.audit_records import InterpretationAuditStep


@dataclass(frozen=True)
class StepGroup:
    """Keep one raw event's bounded audit steps together."""

    raw_id: int
    steps: tuple[InterpretationAuditStep, ...]


@dataclass
class StepBuilder:
    """Collect one raw event's audit steps before they are frozen."""

    raw_id: int
    steps: list[InterpretationAuditStep]


def audit_step(row: sqlite3.Row) -> InterpretationAuditStep:
    """Map one stored step row to its bounded metadata.

    Returns:
        The step metadata without complete bodies.

    """
    kinds = optional_text(row["operation_kinds"])
    return InterpretationAuditStep(
        step_index=int(row["step_index"]),
        stage=str(row["stage"]),
        owner=optional_text(row["owner"]) or optional_text(row["limit_owner"]),
        outcome=optional_text(row["outcome"]),
        observed_byte_length=None if row["observed_byte_length"] is None else int(row["observed_byte_length"]),
        observed_digest=optional_text(row["observed_digest"]),
        diagnostic_code=optional_text(row["diagnostic_code"]),
        reason=optional_text(row["reason"]),
        operation_kinds=() if kinds is None else tuple(kinds.split(",")),
    )


def session_step_groups(rows: list[sqlite3.Row]) -> tuple[StepGroup, ...]:
    """Group ordered step rows by their raw event cursor.

    Returns:
        One bounded step group per raw event.

    """
    builders: list[StepBuilder] = []
    for row in rows:
        raw_id = int(row["raw_id"])
        if builders and builders[-1].raw_id == raw_id:
            builders[-1].steps.append(audit_step(row))
        else:
            builders.append(StepBuilder(raw_id=raw_id, steps=[audit_step(row)]))
    return tuple(map(_freeze, builders))


def _freeze(step_builder: StepBuilder) -> StepGroup:
    return StepGroup(raw_id=step_builder.raw_id, steps=tuple(step_builder.steps))


def optional_text(cell: str | None) -> str | None:
    """Return one nullable text cell without changing its value.

    Returns:
        The cell text, or None.

    """
    return None if cell is None else str(cell)
