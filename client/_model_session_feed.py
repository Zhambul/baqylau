# Copyright (c) 2026 Zhambyl Yermagambet
"""Model session feed."""

from __future__ import annotations

import time
from types import MappingProxyType
from typing import TYPE_CHECKING

from _model_session_state import _ENTRY_PREFIX, _SHELL_PREFIX, _SessionModelState
from _model_shell import ShellFold

if TYPE_CHECKING:
    from collections.abc import Iterator

    from _model_actor import ActorRecord
    from _model_entry import EntryRecord

ATTENTION_TWINS = MappingProxyType({"question_asked": "question_answered", "plan_proposed": "plan_resolved"})


class _SessionModelActors(_SessionModelState):
    """Provide actor lookup operations."""

    def actor(self, actor_id: str) -> ActorRecord | None:
        return self.actors.get(actor_id)

    def actor_name(self, actor_id: str) -> str:
        actor = self.actor(actor_id)
        return actor.name if actor is not None and actor.name else actor_id

    def lead_actor_id(self) -> str:
        return self.session.lead_actor_id

    def lead(self) -> ActorRecord | None:
        return self.actor(self.lead_actor_id())


class _SessionModelFeed(_SessionModelState):
    """Provide feed and running-work queries."""

    def feed(self) -> Iterator[EntryRecord | ShellFold]:
        return map(self._feed_record, self._order)

    def newest_feed(self) -> Iterator[EntryRecord | ShellFold]:
        """Walk the feed from its newest item to its oldest.

        Returns:
            The feed items, newest first.

        """
        return map(self._feed_record, reversed(self._order))

    def keep_newest(self, count: int) -> None:
        """Forget every feed item but the newest `count`.

        A pane keeps only the items that its last paint showed, so its memory
        and its paint do not grow with the session. A late part of a forgotten
        shell is not kept, and a forgotten entry that a frame sends again goes
        back to its old place, where the next paint forgets it again.
        """
        forgotten = len(self._order) - count
        if forgotten <= 0:
            return
        for key in self._order[:forgotten]:
            self._places.pop(key)
            if key.startswith(_SHELL_PREFIX):
                self._shells.pop(key[len(_SHELL_PREFIX) :])
            else:
                self._entries.pop(key[len(_ENTRY_PREFIX) :])
        self._order = self._order[forgotten:]

    def elapsed_since_frame(self) -> float:
        return max(0, time.monotonic() - self._framed_at)

    def running_shell(self, shell_id: str) -> bool:
        return any(shell_id in actor.background.running_shell_ids for actor in self.actors.values())

    def pending_attention(self) -> EntryRecord | None:
        answered = _answered_attention_ids(self._entries)
        for entry in self.newest_feed():
            if isinstance(entry, ShellFold) or entry.type not in ATTENTION_TWINS:
                continue
            if entry.body.attention_id not in answered:
                return entry
        return None

    def _feed_record(self, key: str) -> EntryRecord | ShellFold:
        if key.startswith(_ENTRY_PREFIX):
            return self._entries[key[len(_ENTRY_PREFIX) :]]
        return self._shells[key[len(_SHELL_PREFIX) :]]


def _answered_attention_ids(entries: dict[str, EntryRecord]) -> set[str | None]:
    """Return attention ids that have an answer or decision entry.

    Returns:
        Attention ids that have an answer or decision entry.

    """
    answered: set[str | None] = set()
    for entry in entries.values():
        if entry.type in ATTENTION_TWINS.values():
            answered.add(entry.body.attention_id)
    return answered
