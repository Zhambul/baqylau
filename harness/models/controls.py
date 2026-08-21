"""Every control gesture: what is asked, what it is asked against, what came back.

One dataclass per gesture, each carrying its own `control_name` — the request
type IS the discriminator, so a handler never parses a command word, and the
union below is the whole vocabulary a harness may be asked to perform.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, TypeAlias

from domain.events import PlanProposed, QuestionAsked
from domain.ids import AttentionId, MessageId, ModelId, RequestId, SessionId, WindowId
from domain.values import StructuredContent
from harness.models.session import Session
from terminal.contract import TerminalPlugin


class TitleWriteOutcome(StrEnum):
    """What writing a session's NATIVE title did — the parked-rename path,
    which reaches a store the harness owns rather than the terminal. Was a
    `True / False / None` tri-state whose three meanings were documented
    only in prose."""

    RENAMED = "renamed"            # the harness's own store now carries the new name
    UNSUPPORTED = "unsupported"    # this source is not one this harness can rename
    UNAVAILABLE = "unavailable"    # the store or the row is missing; the caller reports a failure


class AnswerDecision(StrEnum):
    ANSWER = "answer"
    DISCUSS = "discuss"


class ControlAcknowledgement(StrEnum):
    ACKNOWLEDGED = "acknowledged"
    REJECTED = "rejected"
    INDETERMINATE = "indeterminate"


class ConfirmationOutcome(StrEnum):
    CONFIRMED = "confirmed"
    NOT_NEEDED = "not_needed"
    FAILED = "failed"


class ControlName(StrEnum):
    SEND_TEXT = "send_text"
    INTERRUPT = "interrupt"
    BACKGROUND = "background"
    CLOSE_SESSION = "close_session"
    RENAME_SESSION = "rename_session"
    AUTO_NAME_SESSION = "auto_name_session"
    OPEN_REWIND = "open_rewind"
    APPLY_REWIND = "apply_rewind"
    COMPACT = "compact"
    SELECT_MODEL = "select_model"
    SELECT_EFFORT = "select_effort"
    ANSWER_QUESTION = "answer_question"
    READ_PLAN_CHOICES = "read_plan_choices"
    DECIDE_PLAN = "decide_plan"


@dataclass(frozen=True)
class ControlContext:
    # `terminal_window_id` is resolved ONCE, by the service, from the session's
    # own raw event and checked against what the terminal reports — a controller
    # never asks a terminal where a session is. None means the session is not
    # on screen, which most gestures must decline.
    session: Session
    terminal: TerminalPlugin
    terminal_window_id: WindowId | None
    current_effort: str | None
    pending_attention: QuestionAsked | PlanProposed | None


@dataclass(frozen=True)
class ControlTarget:
    session_id: SessionId
    request_id: RequestId


@dataclass(frozen=True)
class AttachmentReference:
    local_path: str
    display_name: str
    media_type: str | None = None


@dataclass(frozen=True)
class SendText(ControlTarget):
    control_name: ClassVar[ControlName] = ControlName.SEND_TEXT
    text: str
    attachments: tuple[AttachmentReference, ...] = ()
    replace_terminal_draft: bool = False


@dataclass(frozen=True)
class Interrupt(ControlTarget):
    control_name: ClassVar[ControlName] = ControlName.INTERRUPT


@dataclass(frozen=True)
class Background(ControlTarget):
    """Move the command that is running right now into the background.

    Carries nothing: WHICH command is the harness's own answer — it is the one
    its TUI is currently blocked on — and a caller that named one would be
    guessing at a race it cannot see.
    """

    control_name: ClassVar[ControlName] = ControlName.BACKGROUND


@dataclass(frozen=True)
class CloseSession(ControlTarget):
    control_name: ClassVar[ControlName] = ControlName.CLOSE_SESSION


@dataclass(frozen=True)
class RenameSession(ControlTarget):
    control_name: ClassVar[ControlName] = ControlName.RENAME_SESSION
    name: str


@dataclass(frozen=True)
class AutoNameSession(ControlTarget):
    control_name: ClassVar[ControlName] = ControlName.AUTO_NAME_SESSION


@dataclass(frozen=True)
class OpenRewind(ControlTarget):
    control_name: ClassVar[ControlName] = ControlName.OPEN_REWIND


@dataclass(frozen=True)
class ApplyRewind(ControlTarget):
    control_name: ClassVar[ControlName] = ControlName.APPLY_REWIND
    target_message_id: MessageId
    target_text: str
    newer_prompt_count: int
    mode: str


@dataclass(frozen=True)
class Compact(ControlTarget):
    control_name: ClassVar[ControlName] = ControlName.COMPACT


@dataclass(frozen=True)
class SelectModel(ControlTarget):
    control_name: ClassVar[ControlName] = ControlName.SELECT_MODEL
    model_id: ModelId


@dataclass(frozen=True)
class SelectEffort(ControlTarget):
    control_name: ClassVar[ControlName] = ControlName.SELECT_EFFORT
    effort: str


@dataclass(frozen=True)
class AnswerQuestion(ControlTarget):
    control_name: ClassVar[ControlName] = ControlName.ANSWER_QUESTION
    attention_id: AttentionId
    decision: AnswerDecision
    answers: StructuredContent | None = None
    discussion: str | None = None


@dataclass(frozen=True)
class ReadPlanChoices(ControlTarget):
    control_name: ClassVar[ControlName] = ControlName.READ_PLAN_CHOICES
    attention_id: AttentionId


@dataclass(frozen=True)
class DecidePlan(ControlTarget):
    control_name: ClassVar[ControlName] = ControlName.DECIDE_PLAN
    attention_id: AttentionId
    decision: str
    feedback: str | None = None


ControlRequest: TypeAlias = (
    SendText
    | Interrupt
    | Background
    | CloseSession
    | RenameSession
    | AutoNameSession
    | OpenRewind
    | ApplyRewind
    | Compact
    | SelectModel
    | SelectEffort
    | AnswerQuestion
    | ReadPlanChoices
    | DecidePlan
)


@dataclass(frozen=True)
class ControlResult:
    request_id: RequestId
    status: ControlAcknowledgement
    reason: str | None = None


@dataclass(frozen=True)
class DeliveryResult(ControlResult):
    queued: bool = False
    restored_text: str = ""
    # An interrupt whose harness confirmed the abort in its OWN raw event (a
    # native record the ordinary translator will read independently) sets
    # this. Unset, an "acknowledged" interrupt is only a screen heuristic —
    # nothing canonical says the turn ended — and the service falls back to
    # `InterruptRegistry` so the busy state can still clear.
    corroborated: bool = False


@dataclass(frozen=True)
class CommandResult(ControlResult):
    confirmation: ConfirmationOutcome | None = None


@dataclass(frozen=True)
class RewindResult(ControlResult):
    restored_text: str = ""
    degraded: bool = False


@dataclass(frozen=True)
class PlanChoice:
    """One decision offered by a harness's LIVE plan dialog.

    Not an `AttentionChoice`, though it was one until the label became that
    type's whole content: here the `digit` is the KEYSTROKE the dialog answers
    to, which the browser has to send back, and it is nothing like a copy of the
    label. `feedback` marks the row that opens the free-text box rather than
    deciding anything.
    """

    digit: str
    label: str
    feedback: bool = False


@dataclass(frozen=True)
class PlanChoicesResult(ControlResult):
    choices: tuple[PlanChoice, ...] = ()


ControlOutcome: TypeAlias = (
    ControlResult
    | DeliveryResult
    | CommandResult
    | RewindResult
    | PlanChoicesResult
)
