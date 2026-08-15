"""The complete public contract implemented by every harness plugin."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar, Literal, Mapping, Protocol, TypeAlias

from contracts.terminal import SessionPaneControl, SessionTerminal, TerminalControl, TerminalScreen
from domain.events import AttentionRequested, CanonicalEvent, EventPayload
from domain.ids import (
    ActorId,
    AttentionId,
    CanonicalEventId,
    MessageId,
    RawEventId,
    SessionId,
)
from domain.values import (
    AccountReference,
    AttentionChoice,
    ModelReference,
    StructuredContent,
)

TranslationDecision: TypeAlias = Literal["translated", "ignored_unknown", "ignored_nonsemantic"]
RecordedTranslationDecision: TypeAlias = TranslationDecision | Literal["translation_failed"]


@dataclass(frozen=True)
class RawEvent:
    raw_event_id: RawEventId
    harness: str
    source_type: str
    source_name: str
    source_position: str
    session_id: SessionId
    actor_id: ActorId
    parent_actor_id: ActorId | None
    observed_at: float
    encoding: str
    payload: bytes


@dataclass(frozen=True)
class SourceCheckpoint:
    session_id: SessionId
    source_identity: str
    position: str


class CheckpointStore(Protocol):
    def load(self, source_identity: str) -> SourceCheckpoint | None: ...
    def commit(self, checkpoint: SourceCheckpoint) -> None: ...


@dataclass(frozen=True)
class TranslationResult:
    events: tuple[CanonicalEvent[EventPayload], ...]
    decision: TranslationDecision
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.decision == "translated" and not self.events:
            raise ValueError("translated observations must produce at least one event")
        if self.decision != "translated" and self.events:
            raise ValueError("ignored observations cannot produce canonical events")


class TranslationError(ValueError):
    def __init__(self, reason: str, *, context: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.context = context


@dataclass(frozen=True)
class IngestionResult:
    raw_event_id: RawEventId
    translation_decision: RecordedTranslationDecision
    accepted_event_ids: tuple[CanonicalEventId, ...]
    deduplicated_event_ids: tuple[CanonicalEventId, ...]
    latest_cursor: int | None


class RawEventDelivery(Protocol):
    def deliver(self, raw_event: RawEvent) -> IngestionResult: ...


@dataclass(frozen=True)
class EventSourceContext:
    session_id: SessionId
    lead_actor_id: ActorId
    actor_id: ActorId
    parent_actor_id: ActorId | None
    source_reference: str


@dataclass(frozen=True)
class RecognizedSession:
    session_id: SessionId
    lead_actor_id: ActorId
    native_session_id: str
    source_reference: str
    working_directory: str | None
    native_process_id: int | None = None

    @property
    def event_source_context(self) -> EventSourceContext:
        return EventSourceContext(
            session_id=self.session_id,
            lead_actor_id=self.lead_actor_id,
            actor_id=self.lead_actor_id,
            parent_actor_id=None,
            source_reference=self.source_reference,
        )


@dataclass(frozen=True)
class ControlContext:
    session: RecognizedSession
    terminal: TerminalControl
    current_model: ModelReference | None
    current_effort: str | None
    current_account: AccountReference | None
    pending_attention: AttentionRequested | None


@dataclass(frozen=True)
class SessionCandidate:
    source_reference: str
    working_directory: str | None = None


class SessionRecognizer(Protocol):
    def discover(self) -> tuple[RecognizedSession, ...]: ...
    def recognize(self, candidate: SessionCandidate) -> RecognizedSession | None: ...


class HarnessEventSource(Protocol):
    def drain(self, delivery: RawEventDelivery) -> None: ...


class HarnessEvents(Protocol):
    def sources(
        self,
        session: RecognizedSession,
        checkpoints: CheckpointStore,
    ) -> tuple[HarnessEventSource, ...]: ...

    def translate(self, raw_event: RawEvent) -> TranslationResult: ...


class HookAction(Protocol):
    """A plugin-owned native action started only after hook facts commit."""

    def start(self) -> None: ...


@dataclass(frozen=True)
class HookIntake:
    session: RecognizedSession
    raw_events: tuple[RawEvent, ...]
    output: bytes = b""
    controls: tuple[ControlRequest, ...] = ()
    actions: tuple[HookAction, ...] = ()


class HarnessHook(Protocol):
    def receive(self, payload: bytes) -> HookIntake: ...


@dataclass(frozen=True)
class SessionLifecycleRequest:
    action: Literal["started", "finished"]


@dataclass(frozen=True)
class SessionLifecycleContext:
    terminal: SessionTerminal
    panes: SessionPaneControl


class HarnessLifecycle(Protocol):
    def apply(
        self,
        request: SessionLifecycleRequest,
        session: RecognizedSession,
        context: SessionLifecycleContext,
    ) -> None: ...


ControlName: TypeAlias = Literal[
    "send_text",
    "interrupt",
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
class PlanChoicesResult(ControlResult):
    choices: tuple[AttentionChoice, ...] = ()


ControlOutcome: TypeAlias = (
    ControlResult
    | DeliveryResult
    | CommandResult
    | RewindResult
    | MigrationResult
    | PlanChoicesResult
)
class ControlHandler(Protocol):
    """One gesture's implementation, registered in `HarnessController.handlers`.

    A Protocol rather than a `Callable[...]` alias because a Callable type carries
    only the parameter TYPES, not their names -- and a renamed parameter is real
    drift here, of exactly the kind that let one harness's lifecycle take
    `recognized_session` where the contract said `session`. Spelling it as
    `__call__` makes the names part of the written contract, and gives the
    handler-signature test one place to read the expected shape from instead of
    repeating it.

    The implementations are plain functions, which satisfy this structurally. They
    cannot DECLARE it the way a class does -- a function subclasses nothing -- so
    the test is the whole of the enforcement, not a backstop to it.
    """

    def __call__(
        self,
        request: ControlRequest,
        context: ControlContext,
    ) -> ControlOutcome: ...


@dataclass(frozen=True)
class HarnessController:
    handlers: Mapping[ControlName, ControlHandler]

    def execute(
        self,
        request: ControlRequest,
        context: ControlContext,
    ) -> ControlOutcome:
        handler = self.handlers.get(request.control_name)
        if handler is None:
            return ControlResult(request_id=request.request_id, status="rejected", reason="unsupported control")
        return handler(request, context)


@dataclass(frozen=True)
class LaunchRequest:
    working_directory: str
    initial_text: str | None
    model_id: str | None
    effort: str | None
    account_id: str | None
    resume_session_id: SessionId | None
    attachments: tuple[AttachmentReference, ...] = ()


@dataclass(frozen=True)
class LaunchResult:
    status: Literal["started", "rejected"]
    window_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class HarnessLaunchPlan:
    command: str
    arguments: tuple[str, ...]
    title: str


class LaunchRejected(ValueError):
    pass


class HarnessLauncher(Protocol):
    def prepare(self, request: LaunchRequest) -> HarnessLaunchPlan: ...


@dataclass(frozen=True)
class ModelOption:
    model_id: str
    display_name: str
    default: bool


@dataclass(frozen=True)
class EffortOption:
    value: str
    display_name: str
    default: bool


@dataclass(frozen=True)
class AccountOption:
    account_id: str
    display_name: str
    available: bool


@dataclass(frozen=True)
class UsageWindow:
    key: str
    label: str
    used_percent: Decimal
    resets_at: float | None
    duration_minutes: int | None
    scope: Literal["account", "model"]
    model_id: str | None


@dataclass(frozen=True)
class UsageBlock:
    model_id: str | None
    message: str | None
    resets_at: float | None


@dataclass(frozen=True)
class UsageRow:
    harness: str
    account_id: str | None
    display_name: str
    switchable: bool
    plan: str | None
    windows: tuple[UsageWindow, ...]
    scheduling_score: Decimal | None
    scheduling_allowed: bool
    limit: UsageBlock | None
    authentication_error: str | None


@dataclass(frozen=True)
class CommandOption:
    command: str
    description: str
    minimum_prompt_count: int


@dataclass(frozen=True)
class RewindModeOption:
    value: str
    display_name: str


@dataclass(frozen=True)
class QueryContext:
    session_id: SessionId | None
    working_directory: str | None


CatalogSection: TypeAlias = Literal[
    "models", "efforts", "accounts", "commands", "rewind_modes", "speech_terms"
]


@dataclass(frozen=True)
class HarnessCatalogSnapshot:
    models: tuple[ModelOption, ...] = ()
    efforts: tuple[EffortOption, ...] = ()
    accounts: tuple[AccountOption, ...] = ()
    commands: tuple[CommandOption, ...] = ()
    rewind_modes: tuple[RewindModeOption, ...] = ()
    speech_terms: tuple[str, ...] = ()


class HarnessCatalog(Protocol):
    sections: frozenset[CatalogSection]

    def read(self, context: QueryContext) -> HarnessCatalogSnapshot: ...


class HarnessUsage(Protocol):
    def read(self) -> tuple[UsageRow, ...]: ...


@dataclass(frozen=True)
class MemoryNoteRecord:
    path: str
    relative_path: str
    name: str
    action: str
    actor_name: str | None
    access_count: int
    accessed_at: float


@dataclass(frozen=True)
class MemorySearchHit:
    path: str
    relative_path: str
    name: str
    line_number: int | None
    title: str
    score: str
    snippet: str


@dataclass(frozen=True)
class MemorySearchRecord:
    command_name: str
    command_action: str
    query: str
    command: str
    expanded_queries: tuple[str, ...]
    hits: tuple[MemorySearchHit, ...]
    actor_name: str | None
    search_count: int
    searched_at: float


@dataclass(frozen=True)
class HarnessMemorySnapshot:
    notes: tuple[MemoryNoteRecord, ...]
    searches: tuple[MemorySearchRecord, ...]


@dataclass(frozen=True)
class MemoryDocument:
    name: str
    path: str
    frontmatter: tuple[tuple[str, str], ...]
    body: str | None
    backlinks: tuple[str, ...]


class HarnessMemory(Protocol):
    def enabled(self, working_directory: str) -> bool: ...
    def item_count(self, session_id: SessionId) -> int: ...
    def snapshot(self, session_id: SessionId) -> HarnessMemorySnapshot: ...
    def document(self, path: str | None, stem: str | None) -> MemoryDocument: ...


@dataclass(frozen=True)
class TerminalInputState:
    typed_text: str | None
    suggestion: str | None


@dataclass(frozen=True)
class TerminalSessionState:
    window_id: str | None
    input_state: TerminalInputState | None


class HarnessTerminalProbe(Protocol):
    def input_state(self, screen: TerminalScreen, window_id: str) -> TerminalInputState | None: ...


@dataclass(frozen=True)
class HarnessInfo:
    name: str
    display_name: str
    plugin_version: str
    canonical_version: int
    supports_attachments: bool = False
    default_for_launch: bool = False


@dataclass(frozen=True)
class HarnessPlugin:
    info: HarnessInfo
    sessions: SessionRecognizer
    events: HarnessEvents
    hook: HarnessHook | None = None
    lifecycle: HarnessLifecycle | None = None
    controller: HarnessController | None = None
    launcher: HarnessLauncher | None = None
    catalog: HarnessCatalog | None = None
    usage: HarnessUsage | None = None
    memory: HarnessMemory | None = None
    terminal_probe: HarnessTerminalProbe | None = None
