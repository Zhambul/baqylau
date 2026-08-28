"""The harness implementation boundary — narrow protocols, one plugin.

A harness is a PLUGIN, the same shape as a terminal plugin: a frozen dataclass
with one typed field per sub-protocol, resolved once at bootstrap and passed
down. Consumers take the field they need — the interpreter takes `sources` +
`translator`, the control service takes `controller` — so what a component can
do to a harness is readable from its constructor.

This file and `harness/models/` know nothing about any concrete CLI, about the
store, or about the HTTP layer. They may name the terminal CONTRACT (a control
context is handed a terminal), which imports nothing of ours, so no cycle can
form.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from typing import Mapping, Protocol

from domain.ids import HarnessName, SessionId, WindowId
from domain.values import ModelReference
from harness.models.catalog import HarnessCatalogSnapshot, QueryContext
from harness.models.controls import (
    AnswerQuestion,
    ApplyRewind,
    AutoNameSession,
    Background,
    CloseSession,
    Compact,
    ControlAcknowledgement,
    ControlContext,
    ControlName,
    ControlOutcome,
    ControlRequest,
    ControlResult,
    DecidePlan,
    Interrupt,
    OpenRewind,
    ReadPlanChoices,
    RenameSession,
    SelectEffort,
    SelectModel,
    SendText,
)
from harness.models.raw_events import RawEvent, TranslationResult
from harness.models.hooks import HarnessHookRequest, HarnessHookResponse
from harness.models.info import HarnessInfo
from harness.models.launch import LaunchRequest, LaunchResult
from harness.models.probe import TerminalInputState
from harness.models.session import Session
from harness.models.telemetry import (
    HarnessTelemetryRequest,
    HarnessTelemetryResponse,
    TelemetryContext,
)
from harness.models.usage import UsageRow
from domain.events import CanonicalEvent, EventPayload
from terminal.contract import TerminalViewport
from terminal.models import SESSION_WINDOW_TAG, WindowInfo

TerminalWindows = tuple[WindowInfo, ...]


def terminal_window_session(window_info: WindowInfo) -> str | None:
    """Return the session tag from one harness-visible terminal window."""
    return window_info.tags.get(SESSION_WINDOW_TAG)


class HarnessRawEventSource(Protocol):
    """One native feed, read as a pure function of a resume position.

    `after_position` is the `source_position` of the last raw event this source
    produced that was recorded — or None on first read. Its encoding is the
    source's own business (a byte offset, a state latch, a snapshot digest); the
    only contract is that `read` returns everything AFTER it, and that a source
    advances past input only by emitting it as a raw event.
    """

    source_identity: str

    def read(self, after_position: str | None) -> tuple[RawEvent, ...]: ...


class HarnessRawEventSources(Protocol):
    def for_session(self, session: Session) -> tuple[HarnessRawEventSource, ...]: ...

    def release_session(self, session_id: SessionId) -> None:
        """Release cached source readers after the session has finished."""
        ...


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

    def handle(self, harness_hook_request: HarnessHookRequest) -> HarnessHookResponse: ...


class HarnessTelemetryGateway(Protocol):
    """One pushed telemetry delivery in, what it meant out.

    The twin of `HarnessHookGateway`. It runs DAEMON-SIDE — the receiver that
    accepted the bytes is a thin HTTP client, exactly like a hook process — so
    it may resolve sessions through the context it is handed.
    """

    def handle(
        self,
        harness_telemetry_request: HarnessTelemetryRequest,
        telemetry_context: TelemetryContext,
    ) -> HarnessTelemetryResponse: ...


class HarnessTranslator(Protocol):
    """Translates the harness's own raw events: hook payloads and native files."""

    def translate(self, raw_event: RawEvent) -> TranslationResult: ...

    def release_session(self, session_id: SessionId) -> None:
        """Release transient correlation after the session has finished."""
        ...


class CoreTranslator(Protocol):
    """Translates raw events our own machinery produces, identically for every
    harness. One small class per core source_type."""

    def translate(self, raw_event: RawEvent) -> TranslationResult: ...


class CanonicalEventReaction(Protocol):
    """One concern of the react phase. Sees every fact that commits, in order.

    A reaction gets everything it needs from the fact itself — raw events never
    reach this layer."""

    def react(self, canonical_event: CanonicalEvent[EventPayload]) -> None: ...


class HarnessReactorContext(Protocol):
    """What the interpreter lends a harness reactor: one typed method per
    gesture a reactor may raise on its own harness's behalf — the same
    surface `HarnessControlService` exposes to the HTTP layer, so a reactor
    calls a named gesture and never builds a bare `ControlRequest`."""

    def send_text(self, send_text: SendText) -> ControlOutcome: ...
    def interrupt(self, interrupt: Interrupt) -> ControlOutcome: ...
    def background(self, background: Background) -> ControlOutcome: ...
    def close_session(self, close_session: CloseSession) -> ControlOutcome: ...
    def rename_session(self, rename_session: RenameSession) -> ControlOutcome: ...
    def auto_name_session(self, auto_name_session: AutoNameSession) -> ControlOutcome: ...
    def open_rewind(self, open_rewind: OpenRewind) -> ControlOutcome: ...
    def apply_rewind(self, apply_rewind: ApplyRewind) -> ControlOutcome: ...
    def compact(self, compact: Compact) -> ControlOutcome: ...
    def select_model(self, select_model: SelectModel) -> ControlOutcome: ...
    def select_effort(self, select_effort: SelectEffort) -> ControlOutcome: ...
    def answer_question(self, answer_question: AnswerQuestion) -> ControlOutcome: ...
    def read_plan_choices(self, read_plan_choices: ReadPlanChoices) -> ControlOutcome: ...
    def decide_plan(self, decide_plan: DecidePlan) -> ControlOutcome: ...


class HarnessCanonicalEventReactor(Protocol):
    """A harness's reaction to committed facts. Runs after the core reaction
    pipeline, for every fact of its own harness — the interpreter dispatches by
    the event's harness, so implementations carry no harness check."""

    def react(
        self, canonical_event: CanonicalEvent[EventPayload], harness_reactor_context: HarnessReactorContext
    ) -> None: ...


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
        control_context: ControlContext,
    ) -> ControlOutcome: ...


@dataclass(frozen=True)
class HarnessController:
    handlers: Mapping[ControlName, ControlHandler]

    def execute(
        self,
        request: ControlRequest,
        control_context: ControlContext,
    ) -> ControlOutcome:
        handler = self.handlers.get(request.control_name)
        if handler is None:
            return ControlResult(
                request_id=request.request_id,
                status=ControlAcknowledgement.REJECTED,
                reason="unsupported control",
            )
        return handler(request, control_context)


class HarnessLauncher(Protocol):
    def launch(self, launch_request: LaunchRequest) -> LaunchResult: ...


class HarnessCatalog(Protocol):
    def read(self, query_context: QueryContext) -> HarnessCatalogSnapshot: ...


class HarnessUsage(Protocol):
    """One harness's current plan-limit rows."""

    def read(self) -> tuple[UsageRow, ...]: ...


class HarnessTerminalProbe(Protocol):
    def input_state(self, terminal_viewport: TerminalViewport, window_id: WindowId) -> TerminalInputState | None: ...


class HarnessResumeLocator(Protocol):
    """Find known native resume commands in terminal process metadata."""

    def locate(
        self,
        windows: tuple[WindowInfo, ...],
    ) -> tuple[tuple[SessionId, WindowId], ...]: ...


class SessionTerminalState(Protocol):
    """The terminal reads required for resume discovery and liveness."""

    def windows(self) -> tuple[WindowInfo, ...]: ...

    def window_for_session(self, session_id: SessionId) -> WindowId | None: ...

    def window_is_live(
        self,
        session_id: SessionId,
        window_id: WindowId,
        windows: tuple[WindowInfo, ...],
    ) -> bool: ...


class SessionResumeRecorder(Protocol):
    """Record one confirmed or discovered native resume launch."""

    def resumed(
        self,
        harness: HarnessName,
        session_id: SessionId,
        window_id: WindowId,
    ) -> None: ...


@dataclass(frozen=True)
class HarnessPlugin:
    """One harness, composed."""

    info: HarnessInfo
    sources: HarnessRawEventSources
    translator: HarnessTranslator
    hooks: HarnessHookGateway | None = None
    telemetry: HarnessTelemetryGateway | None = None
    reactors: tuple[HarnessCanonicalEventReactor, ...] = ()
    controller: HarnessController | None = None
    launcher: HarnessLauncher | None = None
    catalog: HarnessCatalog | None = None
    # THE display name of one of this harness's models — the single owner the
    # catalog, the actor rows and the feed entries all answer through. None
    # means the honest default: the display the source gave, or the native id.
    model_display: Callable[[ModelReference], str] | None = None
    usage: HarnessUsage | None = None
    terminal_probe: HarnessTerminalProbe | None = None
    resume_locator: HarnessResumeLocator | None = None
