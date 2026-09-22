# Copyright (c) 2026 Zhambyl Yermagambet
"""Canonical sessiondata entry support."""

from __future__ import annotations

import typing

from tests import canonical_sessiondata_components as sessiondata_components, canonical_sessiondata_folding as folding

if typing.TYPE_CHECKING:
    from tests.canonical_sessiondata_components import domain as session_domain


def entries_of(
    payload: session_domain.event_base.EventPayload, **kwargs: typing.Unpack[folding.CommittedArguments],
) -> tuple[session_domain.entries.SessionEntry, ...]:
    """Convert one event payload to its ordered feed entries.

    Returns:
        The feed entries, empty if the payload creates none.

    """
    return sessiondata_components.engine.entries.EntryWriter().entries(folding.committed(payload, **kwargs))


def entry_of(
    payload: session_domain.event_base.EventPayload, **kwargs: typing.Unpack[folding.CommittedArguments],
) -> session_domain.entries.SessionEntry | None:
    """Convert one event payload to its first feed entry.

    Returns:
        The first entry, or None if the payload creates no entry.

    """
    entries = entries_of(payload, **kwargs)
    return entries[0] if entries else None


def required_entry(
    payload: session_domain.event_base.EventPayload, **kwargs: typing.Unpack[folding.CommittedArguments],
) -> session_domain.entries.SessionEntry:
    """Return the feed entry that the supplied fact must create.

    Returns:
        The feed entry that the supplied fact must create.

    """
    entry = entry_of(payload, **kwargs)
    assert entry is not None
    return entry


def body_of(
    payload: session_domain.event_base.EventPayload, **kwargs: typing.Unpack[folding.CommittedArguments],
) -> session_domain.entry_base.EntryBody | None:
    """Read the feed body produced by an event payload.

    Returns:
        The body, or None if the payload creates no feed entry.

    """
    entry = entry_of(payload, **kwargs)
    return None if entry is None else entry.body
