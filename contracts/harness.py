"""The complete public contract implemented by every harness plugin."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import ClassVar, Literal, Mapping, Protocol, TypeAlias

from contracts.terminal import TerminalControl, TerminalScreen
from domain.events import (
    AttentionRequested,
    CanonicalEvent,
    EventPayload,
    OperationOutputLocated,
)
from domain.ids import (
    ActorId,
    AttentionId,
    MessageId,
    RawEventId,
    SessionId,
    TurnId,
    stable_event_id,
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
    # Which observer produced this. It is the resume key: the recorder stores it,
    # and a pulled source is resumed from the `source_position` of the LAST
    # recorded raw event carrying its identity. Pushed observers (hooks) have no
    # resume and may leave it at their source_type.
    source_identity: str = ""
    # Set only on hook evidence, None everywhere else. Flat and typed: a hook
    # delivery is the one observation made from INSIDE the session's terminal
    # window and process tree, so what it saw around itself rides its row.
    terminal_window_id: str | None = None
    harness_process_id: int | None = None
    account_id: str | None = None
    account_display_name: str | None = None


@dataclass(frozen=True)
class TranslationResult:
    canonical_events: tuple[CanonicalEvent[EventPayload], ...]
    decision: RecordedTranslationDecision
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.decision == "translated" and not self.canonical_events:
            raise ValueError("translated observations must produce at least one canonical event")
        if self.decision != "translated" and self.canonical_events:
            raise ValueError("ignored observations cannot produce canonical events")


def canonical_event(
    raw_event: RawEvent,
    subject_type: str,
    subject_id: str,
    phase: str,
    payload: EventPayload,
    *,
    turn_id: TurnId | None = None,
    occurred_at: float | None = None,
) -> CanonicalEvent[EventPayload]:
    """One fact from one observation: the identity converges across sources and
    the envelope carries where the observation was made from."""
    return CanonicalEvent(
        event_id=stable_event_id(
            harness=raw_event.harness,
            session_id=raw_event.session_id,
            actor_id=raw_event.actor_id,
            subject_type=subject_type,
            subject_id=subject_id,
            phase=phase,
        ),
        session_id=raw_event.session_id,
        actor_id=raw_event.actor_id,
        turn_id=turn_id,
        parent_actor_id=raw_event.parent_actor_id,
        harness=raw_event.harness,
        occurred_at=occurred_at,
        terminal_window_id=raw_event.terminal_window_id,
        harness_process_id=raw_event.harness_process_id,
        payload=payload,
    )


class TranslationError(ValueError):
    def __init__(self, reason: str, *, context: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.context = context


@dataclass(frozen=True)
class RawEventSourceContext:
    session_id: SessionId
    lead_actor_id: ActorId
    actor_id: ActorId
    parent_actor_id: ActorId | None
    source_reference: str


@dataclass(frozen=True)
class Session:
    """One observed harness session — a read-model derived from committed facts.

    The row is born by the reaction to the session's own `session.started` fact;
    nothing upstream of the store ever requires one. Identity columns are written
    once; the two LIVE columns (`terminal_window_id`, `harness_process_id`) are
    kept current from the envelope of every later hook-borne fact, because a
    resumed session shows up in a new window with a new process. `plugin` is
    attachment, not identity: the server-side `SessionStore` hands out sessions
    with it set, recorder processes leave it None.
    """

    session_id: SessionId
    lead_actor_id: ActorId
    harness_session_id: str
    source_reference: str
    working_directory: str | None
    terminal_window_id: str | None = None
    harness_process_id: int | None = None
    plugin: HarnessPlugin | None = field(default=None, compare=False, repr=False)

    @property
    def source_context(self) -> RawEventSourceContext:
        return RawEventSourceContext(
            session_id=self.session_id,
            lead_actor_id=self.lead_actor_id,
            actor_id=self.lead_actor_id,
            parent_actor_id=None,
            source_reference=self.source_reference,
        )


@dataclass(frozen=True)
class ControlContext:
    session: Session
    terminal: TerminalControl
    current_model: ModelReference | None
    current_effort: str | None
    current_account: AccountReference | None
    pending_attention: AttentionRequested | None


class HarnessRawEventSource(Protocol):
    """One native feed, read as a pure function of a resume position.

    `after_position` is the `source_position` of the last raw event this source
    produced that was recorded — or None on first read. Its encoding is the
    source's own business (a byte offset, a state latch, a snapshot digest); the
    only contract is that `read` returns everything AFTER it, and that a source
    advances past input only by emitting it as evidence.
    """

    source_identity: str

    def read(self, after_position: str | None) -> tuple[RawEvent, ...]: ...


class HarnessRawEventSources(Protocol):
    def for_session(self, session: Session) -> tuple[HarnessRawEventSource, ...]: ...


@dataclass(frozen=True)
class HarnessHookRequest:
    """What one hook shipped: the exact stdin bytes plus what it saw around itself."""

    payload: bytes
    terminal_window_id: str | None
    harness_process_id: int | None
    account_id: str | None
    account_display_name: str | None


@dataclass(frozen=True)
class HarnessHookResponse:
    raw_events: tuple[RawEvent, ...]
    reply: bytes


class HarnessHookGateway(Protocol):
    """One pushed hook delivery → raw events plus the synchronous reply.

    The push twin of `HarnessRawEventSources`: a pull source reads the
    harness's files after a position, a hook gateway receives what the harness
    volunteers. Both produce raw events and nothing else — no store access, no
    terminal, no translation (the interpreter does that on its next tick).

    The request's `payload` is the exact bytes the harness wrote to its hook's
    stdin, and the gateway must embed them unmodified in the raw events it
    returns; the request's flat fields are stamped on the hook raw event. The
    reply bytes go back to the hook's stdout verbatim; a harness with no
    synchronous reply channel returns b"". Rejecting a malformed delivery is
    `raise ValueError`."""

    def handle(self, request: HarnessHookRequest) -> HarnessHookResponse: ...


class HarnessTranslator(Protocol):
    """Translates the harness's own evidence: hook payloads and native files."""

    def translate(self, raw_event: RawEvent) -> TranslationResult: ...


class CoreTranslator(Protocol):
    """Translates evidence our own machinery produces, identically for every
    harness. One small class per core source_type."""

    def translate(self, raw_event: RawEvent) -> TranslationResult: ...


class CanonicalEventReaction(Protocol):
    """One concern of the react phase. Sees every fact that commits, in order.

    A reaction gets everything it needs from the fact itself — raw events never
    reach this layer."""

    def react(self, canonical_event: CanonicalEvent) -> None: ...


class HarnessReactorContext(Protocol):
    """What the interpreter lends a harness reactor: the one control dispatch point."""

    def execute(self, request: ControlRequest) -> ControlOutcome: ...


class HarnessCanonicalEventReactor(Protocol):
    """A harness's reaction to committed facts. Runs after the core reaction
    pipeline, for every fact of its own harness — the interpreter dispatches by
    the event's harness, so implementations carry no harness check."""

    def react(
        self, canonical_event: CanonicalEvent, controls: HarnessReactorContext
    ) -> None: ...


# --- Operation output directives ----------------------------------------------
#
# A hook that makes a command's output observable cannot follow the file itself —
# it must exit immediately. So the gateway records an output-location directive:
# a raw event carrying the typed `OperationOutputLocated` payload. The core
# translator turns it into the fact, the reaction starts the following, and the
# collect phase reads the file's chunks as their own evidence.

OUTPUT_LOCATION_SOURCE_TYPE = "output_location"
LIVENESS_SOURCE_TYPE = "liveness"


def output_location_raw_event(
    context: RawEventSourceContext,
    harness: str,
    located: OperationOutputLocated,
    actor_id: ActorId | None = None,
    parent_actor_id: ActorId | None = None,
) -> RawEvent:
    document = asdict(located)
    document["operation_id"] = str(located.operation_id)
    return RawEvent(
        raw_event_id=RawEventId(
            f"{harness}:output_location:{context.session_id}:{located.operation_id}"
        ),
        harness=harness,
        source_type=OUTPUT_LOCATION_SOURCE_TYPE,
        source_name=located.source_path,
        source_position="located",
        session_id=context.session_id,
        actor_id=actor_id or context.actor_id,
        parent_actor_id=parent_actor_id if actor_id else context.parent_actor_id,
        observed_at=time.time(),
        encoding="json",
        payload=json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8"),
        # NOT the chunk source's identity: the chunk reader resumes from the last
        # raw event under its own identity, and a directive there would
        # masquerade as a read position.
        source_identity=(
            f"{harness}:output_location:{context.session_id}:{located.operation_id}:directive"
        ),
    )


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
class EffortOption:
    value: str
    display_name: str
    default: bool


@dataclass(frozen=True)
class ModelOption:
    """One model a harness offers, with the reasoning levels IT supports.

    The efforts are nested rather than listed once per harness because they are
    model-DEPENDENT: one harness was measured offering a level on some of its
    models and not others, while a single flat list advertised it for all of
    them -- so the picker refused a level the menu had promised. A harness whose
    levels do not vary simply repeats the same tuple on every model.
    """

    model_id: str
    display_name: str
    default: bool
    efforts: tuple[EffortOption, ...] = ()


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


@dataclass(frozen=True)
class HarnessCatalogSnapshot:
    """The menu vocabulary that genuinely depends on WHERE the session is.

    Everything a harness offers unconditionally now lives on HarnessInfo, which
    is a frozen literal built once at import. Only the commands remain here,
    because they are discovered by walking the session's own directory -- two
    sessions in different projects have different ones, so no static literal can
    hold them.
    """

    commands: tuple[CommandOption, ...] = ()


class HarnessCatalog(Protocol):
    def read(self, context: QueryContext) -> HarnessCatalogSnapshot: ...


class HarnessUsage(Protocol):
    def read(self) -> tuple[UsageRow, ...]: ...


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
    """Everything about a harness that does not change while it runs.

    Built once, as a literal, in each plugin's descriptor. That is the whole
    constraint on what may live here: import-time purity forbids file I/O, so a
    fact that has to be READ (the account registry, the session's own slash
    commands) cannot be a field no matter how rarely it changes.
    """

    name: str
    display_name: str
    plugin_version: str
    canonical_version: int
    # The CLI executable's process name — how the hook process finds the CLI in
    # its own ancestry, and how the liveness check tells the CLI apart from a
    # reused pid.
    cli_process_name: str
    supports_attachments: bool = False
    default_for_launch: bool = False
    supports_accounts: bool = False
    models: tuple[ModelOption, ...] = ()
    rewind_modes: tuple[RewindModeOption, ...] = ()


@dataclass(frozen=True)
class HarnessPlugin:
    info: HarnessInfo
    sources: HarnessRawEventSources
    translator: HarnessTranslator
    hooks: HarnessHookGateway | None = None
    reactors: tuple[HarnessCanonicalEventReactor, ...] = ()
    controller: HarnessController | None = None
    launcher: HarnessLauncher | None = None
    catalog: HarnessCatalog | None = None
    usage: HarnessUsage | None = None
    terminal_probe: HarnessTerminalProbe | None = None
