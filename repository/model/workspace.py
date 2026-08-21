"""Row shapes for the four session-workspace tables."""

from __future__ import annotations

from dataclasses import dataclass

from domain.ids import AttentionId, SessionId


@dataclass(frozen=True)
class SessionWorkspaceRow:
    session_id: SessionId
    composer_text: str
    composer_origin: str
    composer_sequence: float
    queue_origin: str
    dialog_attention_id: AttentionId | None
    dialog_origin: str


@dataclass(frozen=True)
class ComposerQueueItemRow:
    session_id: SessionId
    position: int
    text: str


@dataclass(frozen=True)
class DialogAnswerRow:
    session_id: SessionId
    prompt_index: int
    other_text: str


@dataclass(frozen=True)
class DialogAnswerSelectionRow:
    session_id: SessionId
    prompt_index: int
    selection_index: int
    selected_value: str
