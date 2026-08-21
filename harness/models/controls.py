"""Every control gesture: what is asked, what it is asked against, what came back.

One dataclass per gesture, each carrying its own `control_name` — the request
type IS the discriminator, so a handler never parses a command word, and the
union below is the whole vocabulary a harness may be asked to perform.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Literal, TypeAlias

from domain.events import PlanProposed, QuestionAsked
from domain.ids import AttentionId, MessageId, SessionId
from domain.values import (
    AccountReference,
    ModelReference,
    StructuredContent,
)
from harness.models.session import Session
from harness.models.usage import AccountUsageSnapshot
from terminal.contract import TerminalPlugin

# What writing a session's NATIVE title did — the parked-rename path, which
# reaches a store the harness owns rather than the terminal. Was a `True /
# False / None` tri-state whose three meanings were documented only in prose.
TitleWriteOutcome: TypeAlias = Literal[
    "renamed",      # the harness's own store now carries the new name
    "unsupported",  # this source is not one this harness can rename
    "unavailable",  # the store or the row is missing; the caller reports a failure
]

ControlName: TypeAlias = Literal[
    "send_text",
    "interrupt",
    "background",
    "close_session",
    "rename_session",
    "auto_name_session",
    "open_rewind",
    "apply_rewind",
    "migrate_account",
    "compact",
    "select_model",
    "select_effort",
    "answer_question",
    "read_plan_choices",
    "decide_plan",
]


@dataclass(frozen=True)
class ControlContext:
    # `terminal_window_id` is resolved ONCE, by the service, from the session's
    # own evidence and checked against what the terminal reports — a controller
    # never asks a terminal where a session is. None means the session is not
    # on screen, which most gestures must decline.
    session: Session
    terminal: TerminalPlugin
    terminal_window_id: str | None
    current_model: ModelReference | None
    current_effort: str | None
    current_account: AccountReference | None
    pending_attention: QuestionAsked | PlanProposed | None
    # Every account's current plan usage, read ONCE by the service. A value,
    # not a repository: the harness contract names no storage.
    account_usage: tuple[AccountUsageSnapshot, ...] = ()


@dataclass(frozen=True)
class ControlTarget:
    session_id: SessionId
    request_id: str


@dataclass(frozen=True)
class AttachmentReference:
    local_path: str
    display_name: str
    media_type: str | None = None


@dataclass(frozen=True)
class SendText(ControlTarget):
    control_name: ClassVar[ControlName] = "send_text"
    text: str
    attachments: tuple[AttachmentReference, ...] = ()
    replace_terminal_draft: bool = False


@dataclass(frozen=True)
class Interrupt(ControlTarget):
    control_name: ClassVar[ControlName] = "interrupt"


@dataclass(frozen=True)
class Background(ControlTarget):
    """Move the command that is running right now into the background.

    Carries nothing: WHICH command is the harness's own answer — it is the one
    its TUI is currently blocked on — and a caller that named one would be
    guessing at a race it cannot see.
    """

    control_name: ClassVar[ControlName] = "background"


@dataclass(frozen=True)
class CloseSession(ControlTarget):
    control_name: ClassVar[ControlName] = "close_session"


@dataclass(frozen=True)
class RenameSession(ControlTarget):
    control_name: ClassVar[ControlName] = "rename_session"
    name: str


@dataclass(frozen=True)
class AutoNameSession(ControlTarget):
    control_name: ClassVar[ControlName] = "auto_name_session"


@dataclass(frozen=True)
class OpenRewind(ControlTarget):
    control_name: ClassVar[ControlName] = "open_rewind"


@dataclass(frozen=True)
class ApplyRewind(ControlTarget):
    control_name: ClassVar[ControlName] = "apply_rewind"
    target_message_id: MessageId
    target_text: str
    newer_prompt_count: int
    mode: str


@dataclass(frozen=True)
class MigrateAccount(ControlTarget):
    control_name: ClassVar[ControlName] = "migrate_account"


@dataclass(frozen=True)
class Compact(ControlTarget):
    control_name: ClassVar[ControlName] = "compact"


@dataclass(frozen=True)
class SelectModel(ControlTarget):
    control_name: ClassVar[ControlName] = "select_model"
    model_id: str


@dataclass(frozen=True)
class SelectEffort(ControlTarget):
    control_name: ClassVar[ControlName] = "select_effort"
    effort: str


@dataclass(frozen=True)
class AnswerQuestion(ControlTarget):
    control_name: ClassVar[ControlName] = "answer_question"
    attention_id: AttentionId
    decision: Literal["answer", "discuss"]
    answers: StructuredContent | None = None
    discussion: str | None = None


@dataclass(frozen=True)
class ReadPlanChoices(ControlTarget):
    control_name: ClassVar[ControlName] = "read_plan_choices"
    attention_id: AttentionId


@dataclass(frozen=True)
class DecidePlan(ControlTarget):
    control_name: ClassVar[ControlName] = "decide_plan"
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
    | MigrateAccount
    | Compact
    | SelectModel
    | SelectEffort
    | AnswerQuestion
    | ReadPlanChoices
    | DecidePlan
)


@dataclass(frozen=True)
class ControlResult:
    request_id: str
    status: Literal["acknowledged", "rejected", "indeterminate"]
    reason: str | None = None


@dataclass(frozen=True)
class DeliveryResult(ControlResult):
    queued: bool = False
    restored_text: str = ""
    # An interrupt whose harness confirmed the abort in its OWN evidence (a
    # native record the ordinary translator will read independently) sets
    # this. Unset, an "acknowledged" interrupt is only a screen heuristic —
    # nothing canonical says the turn ended — and the service falls back to
    # `InterruptRegistry` so the busy state can still clear.
    corroborated: bool = False


@dataclass(frozen=True)
class CommandResult(ControlResult):
    confirmation: Literal["confirmed", "not_needed", "failed"] | None = None


@dataclass(frozen=True)
class RewindResult(ControlResult):
    restored_text: str = ""
    degraded: bool = False


@dataclass(frozen=True)
class MigrationResult(ControlResult):
    target_account_id: str | None = None


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
    | MigrationResult
    | PlanChoicesResult
)
