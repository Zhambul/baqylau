"""Row shapes for the evidence and fact tables.

One frozen dataclass per table, fields named and ordered exactly as the columns
are, typed as SQLite sees them: `bytes` for BLOB, `int` for the 0/1 booleans,
`str` for the JSON columns. No methods, no defaults, no validation — the mapper
does all three.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SessionRow:
    session_id: str
    lead_actor_id: str
    harness: str
    harness_session_id: str
    source_reference: str
    working_directory: str | None
    terminal_window_id: str | None
    harness_process_id: int | None
    created_at: float


@dataclass(frozen=True)
class RawEventRow:
    id: int
    raw_event_id: str
    session_id: str
    harness: str
    source_type: str
    source_identity: str
    source_name: str
    source_position: str
    actor_id: str
    parent_actor_id: str | None
    observed_at: float
    encoding: str
    payload: bytes
    terminal_window_id: str | None
    harness_process_id: int | None
    account_id: str | None
    account_display_name: str | None


@dataclass(frozen=True)
class CanonicalEventRow:
    cursor: int
    event_id: str
    schema_version: int
    event_type: str
    session_id: str
    actor_id: str
    turn_id: str | None
    parent_actor_id: str | None
    harness: str
    occurred_at: float | None
    terminal_window_id: str | None
    harness_process_id: int | None
    accepted_at: float
    payload: str


@dataclass(frozen=True)
class OperationOutputRow:
    session_id: str
    operation_id: str
    harness: str
    actor_id: str
    parent_actor_id: str | None
    source_path: str
    chunk_source_type: str
    delete_source: int
    initial_size: int
    initial_modified_at: int
    wait_for_source_change: int
    until: str
    state: str
    created_at: float
