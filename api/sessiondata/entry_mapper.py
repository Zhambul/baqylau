# Copyright (c) 2026 Zhambyl Yermagambet
"""Map canonical entries to API entry responses."""

from __future__ import annotations

from typing import TYPE_CHECKING

from api.sessiondata import entry_body_mapper
from api.sessiondata.models import entry as entry_models

if TYPE_CHECKING:
    from domain import entries as domain_entries
    from repository.contract.session_data import EntryPage


def entry_page(current_entry_page: EntryPage) -> entry_models.EntryPageResponse:
    """Return the API response for one entry page.

    Returns:
        The API response for one entry page.

    """
    return entry_models.EntryPageResponse(
        items=tuple(entry(session_entry) for session_entry in current_entry_page.entries),
        oldest_cursor=current_entry_page.oldest_cursor,
        has_more=current_entry_page.has_more,
    )


def entry(session_entry: domain_entries.SessionEntry) -> entry_models.EntryResponse:
    """Return the API response for one session entry.

    Returns:
        The API response for one session entry.

    """
    return entry_models.EntryResponse(
        entry_id=str(session_entry.entry_id),
        type=session_entry.entry_type,
        cursor=session_entry.cursor,
        commit_cursor=session_entry.commit_cursor,
        actor_id=str(session_entry.actor_id),
        parent_actor_id=None if session_entry.parent_actor_id is None else str(session_entry.parent_actor_id),
        turn_id=None if session_entry.turn_id is None else str(session_entry.turn_id),
        occurred_at=session_entry.occurred_at,
        summary=session_entry.summary,
        body=entry_body_mapper.entry_body(session_entry.body),
    )
