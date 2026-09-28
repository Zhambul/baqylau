# Copyright (c) 2026 Zhambyl Yermagambet
"""Verify terminal client architecture and behavior."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Iterator

TYPE_FIELD = "type"


CURSOR_FIELD = "cursor"


ACTOR_ID_FIELD = "actor_id"


LEAD_ACTOR_ID_TEXT = "lead"


type JsonValue = bool | float | int | str | list[JsonValue] | dict[str, JsonValue] | None


@runtime_checkable
class PathRoute(Protocol):
    """A route with a public URL path."""

    path: str


@runtime_checkable
class RouterInclusion(Protocol):
    """A route that includes another router."""

    original_router: object


class PaneModel(Protocol):
    """Describe the pane model fields used by these tests."""

    def apply_snapshot(self, snapshot: dict[str, JsonValue]) -> None:
        """Apply one snapshot."""

    def feed(self) -> Iterator[PaneFeedRecord]:
        """Return pane feed records."""


class PaneFeedRecord(Protocol):
    """Describe one pane feed record."""

    entry_id: str
    type: str


def _pane_entry(
    entry_id: str,
    kind: str,
    body: dict[str, JsonValue],
    *,
    actor_id: str,
    occurred_at: float = 1.0,
) -> dict[str, JsonValue]:
    return {
        "entry_id": entry_id,
        TYPE_FIELD: kind,
        CURSOR_FIELD: int(entry_id),
        "commit_cursor": int(entry_id),
        ACTOR_ID_FIELD: actor_id,
        "parent_actor_id": None,
        "turn_id": None,
        "occurred_at": occurred_at,
        "summary": None,
        "body": body,
    }


def child_pane_entry(
    entry_id: str,
    kind: str,
    body: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    """Make an entry for the child actor.

    Returns:
        The child entry document.

    """
    return _pane_entry(entry_id, kind, body, actor_id="kid")


def lead_pane_entry(
    entry_id: str,
    kind: str,
    body: dict[str, JsonValue],
    occurred_at: float = 1.0,
) -> dict[str, JsonValue]:
    """Make an entry for the lead actor.

    Returns:
        The lead entry document.

    """
    return _pane_entry(entry_id, kind, body, actor_id=LEAD_ACTOR_ID_TEXT, occurred_at=occurred_at)
