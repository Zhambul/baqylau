# Copyright (c) 2026 Zhambyl Yermagambet
"""Model session state."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from _model_entry import EntryPageDocument, EntryRecord, SnapshotDocument, StreamFrameDocument
from _model_shell import ShellFold

if TYPE_CHECKING:
    from _model_actor import ActorRecord
    from _model_base import SessionRecord

_ENTRY_PREFIX = "entry:"
_SHELL_PREFIX = "shell:"
SHELL_ENTRIES = frozenset(("shell_started", "shell_output", "shell_backgrounded", "shell_finished"))


class _SessionModelState:
    """Declare state shared by session-model roles."""

    cursor: int
    entry_cursor: int
    session: SessionRecord
    actors: dict[str, ActorRecord]
    live: bool
    _framed_at: float
    _order: list[str]
    _entries: dict[str, EntryRecord]
    _shells: dict[str, ShellFold]
    _dropped: set[str]


class _SessionModelIngress(_SessionModelState):
    """Apply snapshots, pages, and stream frames."""

    def apply_snapshot(self, document: SnapshotDocument) -> None:
        document = SnapshotDocument.model_validate(document)
        self._framed_at = time.monotonic()
        self.cursor = document.cursor
        self.session = document.session
        self.live = document.live
        self.actors = {actor.actor_id: actor for actor in document.actors}

    def apply_page(self, document: EntryPageDocument) -> None:
        document = EntryPageDocument.model_validate(document)
        for entry in document.entries:
            self._apply_entry(entry)

    def apply_frame(self, document: StreamFrameDocument) -> None:
        """Apply one stream frame."""
        document = StreamFrameDocument.model_validate(document)
        self._framed_at = time.monotonic()
        if document.session is not None:
            self.session = document.session
        for actor in document.actors:
            self.actors[actor.actor_id] = actor
        for entry in document.entries:
            self._apply_entry(entry)

    def _apply_entry(self, entry: EntryRecord) -> None:
        entry_id = entry.entry_id
        if entry_id in self._entries or entry_id in self._dropped:
            return
        # An entry's cursor is its row, not a canonical cursor; the stream takes it as `after_entry`.
        self.entry_cursor = max(self.entry_cursor, entry.cursor)
        self._entries[entry_id] = entry
        if entry.type in SHELL_ENTRIES:
            self._fold_shell(entry)
            return
        self._order.append(_ENTRY_PREFIX + entry_id)
        if entry.type == "message":
            self._drop_superseded(entry)

    def _fold_shell(self, entry: EntryRecord) -> None:
        shell_id = entry.body.shell_id
        fold = self._shells.get(shell_id)
        if fold is None:
            if entry.type != "shell_started":
                return
            self._shells[shell_id] = ShellFold.from_entry(entry)
            self._order.append(_SHELL_PREFIX + shell_id)
            return
        fold.fold(entry)

    def _drop_superseded(self, entry: EntryRecord) -> None:
        replaced = entry.body.reply_to
        if not replaced or not _is_prompt(entry):
            return
        surviving = entry.entry_id
        for key in list(self._order):
            if key.startswith(_ENTRY_PREFIX):
                other = self._entries[key[len(_ENTRY_PREFIX) :]]
                if _is_superseded_prompt(other, surviving, replaced):
                    self._drop_entry(key, other)

    def _drop_entry(self, key: str, entry: EntryRecord) -> None:
        self._order.remove(key)
        self._entries.pop(entry.entry_id, None)
        self._dropped.add(entry.entry_id)


def _is_prompt(entry: EntryRecord) -> bool:
    return entry.type == "message" and entry.body.role == "user"


def _is_superseded_prompt(entry: EntryRecord, surviving: str, replaced: str) -> bool:
    return entry.entry_id != surviving and _is_prompt(entry) and entry.body.reply_to == replaced
