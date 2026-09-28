# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide the stream frame module."""

# The two SSE frames, and they are the same shape twice: whatever changed.
#
# One frame per poll that found news, carrying everything found — so ten context
# reports inside one poll window collapse into one actor object, and the poll
# interval IS the batch boundary. No coalescing rule exists because none is
# needed.
from pydantic import BaseModel

from api.sessiondata.models.actor import ActorResponse
from api.sessiondata.models.entry import EntryResponse
from api.sessiondata.models.session_data import SessionResponse


class SessionStreamFrame(BaseModel):
    """Represent session stream frame.

    One session's news. Every part is absent when it did not change; the
        frame's `id` is the highest cursor it carries, which is what the client
        sends back as Last-Event-ID after a drop, a sleep or a daemon restart.
    """

    session: SessionResponse | None = None
    actors: tuple[ActorResponse, ...] = ()
    entries: tuple[EntryResponse, ...] = ()


class ViewReset(BaseModel):
    """Tell a stream client that a switch rewrote rows that it already has.

    The client reads a new snapshot and reconnects with this view revision.
    """

    view_revision: int


class GlobalStreamFrame(BaseModel):
    """Represent global stream frame.

    The same, across every session, and without the feed: this drives the
        list and the tab colours, neither of which reads an entry.
    """

    sessions: tuple[SessionResponse, ...] = ()
    actors: tuple[ActorResponse, ...] = ()
