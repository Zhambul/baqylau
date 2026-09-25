# Copyright (c) 2026 Zhambyl Yermagambet
"""Model session."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from _model_base import SessionRecord
from _model_session_feed import _SessionModelActors, _SessionModelFeed
from _model_session_state import _SessionModelIngress

if TYPE_CHECKING:
    from _model_actor import ActorRecord
    from _model_entry import EntryRecord
    from _model_shell import ShellFold


class SessionModel(_SessionModelIngress, _SessionModelActors, _SessionModelFeed):
    """SessionData plus the feed, at one cursor.

    The three inputs are the three shapes the daemon serves — the snapshot, a
    page of entries, and a stream frame — and each one lands through its own
    method so that nothing has to guess which it was given. Entry application is
    idempotent by `entry_id`: an overlapping frame after a reconnect is applied
    twice and shows once.
    """

    def __init__(self) -> None:
        self.cursor = 0
        # The highest entry row: a projection commits some entries after their fact's cursor.
        self.entry_cursor = 0
        self.session = SessionRecord()
        self.actors: dict[str, ActorRecord] = {}
        self.live = False
        # When this model last took a frame, on a monotonic clock. The only thing
        # it is for is carrying a running clock forward between frames: the
        # daemon measures elapsed time when it BUILDS a frame, and frames arrive
        # on change, not on a tick.
        self._framed_at = time.monotonic()
        # First-appearance order, and the two things that order can hold: an
        # entry as it arrived, or a command being folded. A shell takes the
        # position of its START and grows in place, which is what makes its
        # output land under its own command instead of at the end of the feed.
        self._order: list[str] = []
        self._entries: dict[str, EntryRecord] = {}
        self._shells: dict[str, ShellFold] = {}
        # Entries this model has already decided are dead. Remembered, not just
        # removed: a reconnect re-sends an overlapping page, and a discarded
        # prompt that was merely deleted would be re-admitted as news — and stay,
        # because the survivor that condemned it is applied only once.
        self._dropped: set[str] = set()
