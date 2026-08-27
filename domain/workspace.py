"""Local composer work and the durable mirror of the harness message queue.

The message you are still typing and the option you highlighted in a dialog are
local work. A queued message is a harness fact. It is stored here as a read
model so the UI can restore it after a reload.

These shapes lived inside the dashboard service that also wrote their SQL. They
are here so the repository can hand them back and the service can stay a
service.
"""

from __future__ import annotations

from dataclasses import dataclass

from domain.ids import AttentionId, RequestId, SessionId


@dataclass(frozen=True)
class ComposerDraft:
    text: str
    origin: str
    sequence: float


@dataclass(frozen=True)
class QueuedMessage:
    request_id: RequestId
    text: str


@dataclass(frozen=True)
class ComposerQueue:
    items: tuple[QueuedMessage, ...]
    origin: str


@dataclass(frozen=True)
class ComposerState:
    draft: ComposerDraft | None
    queue: ComposerQueue | None


@dataclass(frozen=True)
class AnswerSelection:
    selected: tuple[str, ...]
    other: str


@dataclass(frozen=True)
class DialogDraft:
    attention_id: AttentionId
    answers: tuple[AnswerSelection, ...]
    origin: str


@dataclass(frozen=True)
class DialogState:
    draft: DialogDraft | None


@dataclass(frozen=True)
class SessionWorkspace:
    """Everything stored against one session, exactly as stored.

    Unfiltered on purpose: dropping a draft whose text has since been delivered,
    or a dialog whose attention is no longer pending, needs canonical facts —
    so it belongs to the service, not to the store.
    """

    session_id: SessionId
    draft: ComposerDraft | None = None
    queue: ComposerQueue | None = None
    dialog: DialogDraft | None = None
