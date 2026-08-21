# What YOU have on one session: the density you chose, the message you are
# still typing, the ones you queued behind it, the option you highlighted in a
# dialog — plus the errors the daemon swallowed while working on it.
from pydantic import BaseModel

from domain.ids import AttentionId
from domain.preferences import ViewMode

from api.common.models.values.terminal_state import TerminalStateResponse


class SessionPreferencesResponse(BaseModel):
    view_mode: ViewMode
    notifications_muted: bool
    tasks_hidden: bool


class ComposerDraftResponse(BaseModel):
    text: str
    origin: str
    sequence: float


class QueuedMessageResponse(BaseModel):
    text: str


class ComposerQueueResponse(BaseModel):
    items: tuple[QueuedMessageResponse, ...]
    origin: str


class ComposerStateResponse(BaseModel):
    draft: ComposerDraftResponse | None
    queue: ComposerQueueResponse | None


class AnswerSelectionResponse(BaseModel):
    selected: tuple[str, ...]
    other: str


class DialogDraftResponse(BaseModel):
    attention_id: AttentionId
    answers: tuple[AnswerSelectionResponse, ...]
    origin: str


class DialogStateResponse(BaseModel):
    draft: DialogDraftResponse | None


class ApplicationErrorResponse(BaseModel):
    error_id: int
    timestamp: float
    component: str
    action: str
    traceback: str
    context: str


class SessionApplicationResponse(BaseModel):
    preferences: SessionPreferencesResponse
    composer: ComposerStateResponse
    dialog: DialogStateResponse
    terminal: TerminalStateResponse
    errors: tuple[ApplicationErrorResponse, ...]
