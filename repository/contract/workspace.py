"""Your unsent work on one session, stored across four tables.

`find` assembles them into one `SessionWorkspace`, UNFILTERED: dropping a draft
whose text has since been delivered, or a dialog whose attention is no longer
pending, needs canonical facts and belongs to the service above.
"""

from __future__ import annotations

from typing import Protocol

from domain.ids import SessionId
from domain.workspace import ComposerDraft, ComposerQueue, DialogDraft, SessionWorkspace


class SessionWorkspaceRepository(Protocol):
    def find(self, session_id: SessionId) -> SessionWorkspace | None: ...

    def save_composer_draft(self, session_id: SessionId, draft: ComposerDraft) -> bool:
        """Save the newest browser draft; False for an older concurrent write.

        The compare and the write are one transaction: two request threads each
        own a connection, so a get-then-set would let the second clobber the
        first with a stale sequence.
        """
        ...

    def save_composer_queue(self, session_id: SessionId, queue: ComposerQueue) -> None:
        """Replace the whole queue in one transaction."""
        ...

    def save_dialog_draft(self, session_id: SessionId, draft: DialogDraft) -> None:
        """Replace the whole half-made answer set in one transaction."""
        ...
