"""Your unsent work on one session — the state the session itself never sees.

The message you are still typing, the ones you queued behind it, the option you
highlighted in a dialog. Not a fact about the session: a fact about you, which
is why it is stored rather than folded from raw events.

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
