"""Local composer work and the harness queue mirror, stored across four tables.

`find` assembles them into one `SessionWorkspace`, UNFILTERED. Canonical facts
add and remove queue items, drop a delivered draft, and drop a dialog whose
attention is no longer pending. That filtering belongs to the service above.
"""

from __future__ import annotations

from typing import Protocol

from domain.ids import RequestId, SessionId
from domain.workspace import ComposerDraft, DialogDraft, QueuedMessage, SessionWorkspace


class SessionWorkspaceRepository(Protocol):
    def find(self, session_id: SessionId) -> SessionWorkspace | None: ...

    def save_composer_draft(self, session_id: SessionId, composer_draft: ComposerDraft) -> bool:
        """Save the newest browser draft; False for an older concurrent write.

        The compare and the write are one transaction: two request threads each
        own a connection, so a get-then-set would let the second clobber the
        first with a stale sequence.
        """
        ...

    def enqueue_composer_message(
        self,
        session_id: SessionId,
        queued_message: QueuedMessage,
        origin: str,
    ) -> None:
        """Append one accepted send once; the request ID is idempotent."""
        ...

    def remove_queued_message(
        self,
        session_id: SessionId,
        request_id: RequestId,
    ) -> None:
        """Remove one queued send after its canonical prompt arrives."""
        ...

    def save_dialog_draft(self, session_id: SessionId, dialog_draft: DialogDraft) -> None:
        """Replace the whole half-made answer set in one transaction."""
        ...
