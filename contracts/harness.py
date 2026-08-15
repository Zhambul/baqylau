"""The complete public contract implemented by every harness plugin."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import ClassVar, Literal, Mapping, Protocol, TypeAlias

from contracts.terminal import TerminalControl, TerminalScreen
from domain.events import AttentionRequested, CanonicalEvent, EventPayload
from domain.ids import (
    ActorId,
    AttentionId,
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
    # Which observer produced this. It is the resume key: the recorder stores it,
    # and a pulled source is resumed from the `source_position` of the LAST
    # recorded raw event carrying its identity. Pushed observers (hooks) have no
    # resume and may leave it at their source_type.
    source_identity: str = ""


@dataclass(frozen=True)
class TranslationResult:
    canonical_events: tuple[CanonicalEvent[EventPayload], ...]
    decision: TranslationDecision
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.decision == "translated" and not self.canonical_events:
            raise ValueError("translated observations must produce at least one canonical event")
        if self.decision != "translated" and self.canonical_events:
            raise ValueError("ignored observations cannot produce canonical events")


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
    """One observed harness session.

    The row is written ONCE, at launch, by the wrapper that started the harness
    (`SessionRegistry.register`); everything that changes over the session's
    life is a canonical fact, never an update here. `plugin` is attachment, not
    identity: the server-side `SessionRegistry` hands out sessions with it set,
    recorder processes leave it None.
    """

    session_id: SessionId
    lead_actor_id: ActorId
    native_session_id: str
    source_reference: str
    working_directory: str | None
    native_process_id: int | None = None
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


class HarnessCanonicalTranslator(Protocol):
    def translate(self, raw_event: RawEvent) -> TranslationResult: ...


class HarnessSessionEvidence(Protocol):
    """Derive a session from its own orphan evidence.

    The wrapper registers a session at launch; for every other launch path the
    evidence itself announces the session — a hook payload carries the identity
    and the source reference. The interpreter calls this for raw events whose
    session has no row yet; None means this particular observation cannot name
    one (it keeps trying with later evidence)."""

    def from_raw_event(self, raw_event: RawEvent) -> Session | None: ...


class HarnessReactorContext(Protocol):
    """What the interpreter lends a reactor: the one control dispatch point."""

    def execute(self, request: ControlRequest) -> ControlOutcome: ...


class HarnessReactor(Protocol):
    """Harness-specific reactions to committed evidence, run by the interpreter.

    Called once per raw event, AFTER its translation committed — never inside a
    recorder process. This is where a harness starts companion processes or
    keeps plugin-private bookkeeping that hooks used to do inline.
    """

    def react(self, raw_event: RawEvent, context: HarnessReactorContext) -> None: ...


# --- File watches ----------------------------------------------------------------
#
# A hook that makes a command's output observable cannot follow the file itself —
# it must exit immediately. So it records a `watch` raw event: a directive-as-
# evidence saying "this path holds operation X's output, read it". The
# interpreter applies these to its `watches` table and pulls the file with a
# generic source until a matching finish directive (and EOF) ends it.

WATCH_SOURCE_TYPE = "watch"


@dataclass(frozen=True)
class FileWatch:
    operation_id: str
    source_path: str
    chunk_source_type: str
    delete_source: bool
    initial_size: int
    initial_modified_at: int
    wait_for_source_change: bool


def watch_start_raw_event(
    context: RawEventSourceContext,
    harness: str,
    watch: FileWatch,
    actor_id: ActorId | None = None,
    parent_actor_id: ActorId | None = None,
) -> RawEvent:
    document = {
        "action": "start",
        "operation_id": watch.operation_id,
        "source_path": watch.source_path,
        "chunk_source_type": watch.chunk_source_type,
        "delete_source": watch.delete_source,
        "initial_size": watch.initial_size,
        "initial_modified_at": watch.initial_modified_at,
        "wait_for_source_change": watch.wait_for_source_change,
    }
    return _watch_raw_event(context, harness, watch.operation_id, document, actor_id, parent_actor_id)


def watch_finish_raw_event(
    context: RawEventSourceContext,
    harness: str,
    operation_id: str,
) -> RawEvent:
    document = {"action": "finish", "operation_id": operation_id}
    return _watch_raw_event(context, harness, operation_id, document, None, None)


def _watch_raw_event(
    context: RawEventSourceContext,
    harness: str,
    operation_id: str,
    document: dict,
    actor_id: ActorId | None,
    parent_actor_id: ActorId | None,
) -> RawEvent:
    action = document["action"]
    return RawEvent(
        raw_event_id=RawEventId(f"{harness}:watch:{context.session_id}:{operation_id}:{action}"),
        harness=harness,
        source_type=WATCH_SOURCE_TYPE,
        source_name=str(document.get("source_path") or operation_id),
        source_position=action,
        session_id=context.session_id,
        actor_id=actor_id or context.actor_id,
        parent_actor_id=parent_actor_id if actor_id else context.parent_actor_id,
        observed_at=time.time(),
        encoding="json",
        payload=json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8"),
        # NOT the chunk source's identity: the interpreter resumes the chunk
        # reader from the last raw event under `<harness>:watch:<session>:<op>`,
        # and a directive there would masquerade as a read position.
        source_identity=f"{harness}:watch:{context.session_id}:{operation_id}:directive",
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
    supports_attachments: bool = False
    default_for_launch: bool = False
    supports_accounts: bool = False
    models: tuple[ModelOption, ...] = ()
    rewind_modes: tuple[RewindModeOption, ...] = ()


@dataclass(frozen=True)
class HarnessPlugin:
    info: HarnessInfo
    sources: HarnessRawEventSources
    translator: HarnessCanonicalTranslator
    session_evidence: HarnessSessionEvidence | None = None
    reactor: HarnessReactor | None = None
    controller: HarnessController | None = None
    launcher: HarnessLauncher | None = None
    catalog: HarnessCatalog | None = None
    usage: HarnessUsage | None = None
    memory: HarnessMemory | None = None
    terminal_probe: HarnessTerminalProbe | None = None
