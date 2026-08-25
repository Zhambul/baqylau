"""Every node of the application, one provider each, wired by its signature.

The one place that knows WHICH harnesses and which terminal are installed, and
the one place that opens a database. A provider declares what it needs as
`Annotated[T, Depends(...)]` parameters, so the graph is a declaration FastAPI
reads rather than an object assembled here — a route, a background thread and a
test all ask for the node they actually use, and get the same instance
(`app/injection.py` `singleton`).

Every node is a process singleton: a `SqliteDatabase` initialises its schema
once, and the services holding cross-request state (the session list's warm
cache, the notification revision, the usage rows, the interpreter) must be ONE
object. The memo lives on the application, not on this module.

Below the contract line nothing changes: a repository still takes a handle and
opens a short-lived connection per call, and no service resolves a path.

NO postponed annotations in this module, deliberately — see `app/injection.py`.
"""

import os
from typing import Annotated, Mapping

from fastapi import Depends

from app.injection import singleton
from app.services.insights import ApplicationInsightsService
from app.services.resume import ResumableSessionService
from app.services.uploads import UploadService
from core import data
from core.repository import RepositoryQueries
from dashboard import config as dashboard_config
from dashboard.services.notices import DashboardNotificationState
from dashboard.services.preferences import ApplicationPreferenceService
from dashboard.services.workspace import (
    QueuedPromptCanonicalEventReaction,
    SessionApplicationService,
)
from audit.recorder import AuditRecorder
from audit.telemetry import BrowserTelemetryService
from engine.interpret.loop import Interpreter
from engine.react.loop import ReactionLoop
from engine.sessiondata.actors import (
    ActorWriter,
    ContextWriter,
    StatisticsWriter,
    StatusWriter,
    UsageWriter,
)
from engine.sessiondata.naming import ModelNaming
from engine.sessiondata.contract import (
    AppliedActorListener,
    SessionDataWriter,
    SessionEntryWriter,
)
from engine.sessiondata.entries import EntryWriter
from engine.sessiondata.session import GoalWriter, SessionWriter, TaskWriter
from engine.interpret.reactions import (
    InterruptCanonicalEventReaction,
    ShellOutputCanonicalEventReaction,
    SessionUpsertCanonicalEventReaction,
)
from engine.interpret.translators import (
    AutomaticTitleTranslator,
    ControlTranslator,
    InterruptTranslator,
    LivenessTranslator,
    ResumeLivenessTranslator,
    SessionResumeTranslator,
    ShellOutputTranslator,
)
from harness.contract import CanonicalEventReaction, CoreTranslator
from harness.hooks.gateway import HookGatewayService
from harness.impl import installed
from harness.models import (
    AUTOMATIC_TITLE_SOURCE_TYPE,
    CONTROL_SOURCE_TYPE,
    INTERRUPT_SOURCE_TYPE,
    LIVENESS_SOURCE_TYPE,
    OUTPUT_LOCATION_SOURCE_TYPE,
    RESUME_LIVENESS_SOURCE_TYPE,
    RESUME_SOURCE_TYPE,
    InterruptRegistry,
)
from harness.registry import HarnessRegistry
from harness.services.catalog import HarnessCatalogService
from harness.services.control_effects import ControlEffectRecorder
from harness.services.controls import HarnessControlService
from harness.services.launcher import HarnessLauncherService
from harness.services.launch_effects import SessionLaunchEffectRecorder
from harness.services.probe import TerminalInputService
from harness.services.telemetry import TelemetryGatewayService
from harness.services.usage import ApplicationUsageState, HarnessUsageService
from inference.contract import ModelFactory
from inference.default import DefaultModelFactory
from naming.jobs import AutomaticNamingReaction, NamingJobWorker
from naming.service import AutomaticSessionNamer
from notify.presence import Presence
from repository.contract.audit import (
    AuditReadRepository,
    AuditWriteRepository,
)
from repository.contract.facts import (
    CanonicalEventRepository,
    RawEventRepository,
)
from repository.contract.session_data import SessionDataRepository
from repository.contract.naming import NamingJobRepository
from repository.contract.shell_output import ShellOutputRepository
from repository.contract.preferences import (
    HiddenDirectoryRepository,
    NewSessionRepository,
    NotificationSettingRepository,
    PushSigningKeyRepository,
    PushSubscriptionRepository,
    TaskDismissalRepository,
    ViewModeRepository,
)
from repository.contract.sessions import SessionRepository
from repository.contract.terminal import PaneWidthRepository
from repository.contract.uploads import UploadRepository
from repository.contract.usage import AccountUsageRepository
from repository.contract.workspace import SessionWorkspaceRepository
from repository.contract.diagnostics import DiagnosticsRepository
from repository.impl.sqlite.canonical_events import SqliteCanonicalEventRepository
from repository.impl.sqlite.diagnostics import SqliteDiagnosticsRepository
from repository.impl.sqlite.connection import SqliteDatabase
from repository.impl.sqlite.databases import audit_database, main_database, read_only
from repository.impl.sqlite.audit import (
    SqliteAuditReadRepository,
    SqliteAuditWriteRepository,
)
from repository.impl.sqlite.session_data import SqliteSessionDataRepository
from repository.impl.sqlite.naming import SqliteNamingJobRepository
from repository.impl.sqlite.shell_output import SqliteShellOutputRepository
from repository.impl.sqlite.preferences import (
    SqliteHiddenDirectoryRepository,
    SqliteNewSessionRepository,
    SqliteNotificationSettingRepository,
    SqlitePushSigningKeyRepository,
    SqlitePushSubscriptionRepository,
    SqliteTaskDismissalRepository,
    SqliteViewModeRepository,
)
from repository.impl.sqlite.raw_events import SqliteRawEventRepository
from repository.impl.sqlite.sessions import SqliteSessionRepository
from repository.impl.sqlite.terminal import (
    SqlitePaneWidthRepository,
)
from repository.impl.sqlite.uploads import SqliteUploadRepository
from repository.impl.sqlite.usage import SqliteAccountUsageRepository
from repository.impl.sqlite.workspace import SqliteSessionWorkspaceRepository
from terminal.adapter import TerminalAdapter
from terminal.contract import TerminalPlugin
from terminal.impl import resolve as resolve_terminal
from terminal.impl.null import null_plugin
from terminal.impl.pty.plugin import pty_plugin
from terminal.panes.commands import PaneCommandService
from terminal.panes.reaction import PaneCanonicalEventReaction
from terminal.services.panes import PaneWidthService
from terminal.tabs import TabColorPainter

# --- the two files ------------------------------------------------------------
# One handle each, one initialize each. The paths are core/data.py's answer, so
# a test moves all three databases with one environment variable.


@singleton
def main_db() -> SqliteDatabase:
    return main_database(data.main_database_path())


MainDb = Annotated[SqliteDatabase, Depends(main_db)]


@singleton
def audit_db() -> SqliteDatabase:
    return audit_database(data.audit_database_path())


AuditDb = Annotated[SqliteDatabase, Depends(audit_db)]


@singleton
def audit_reader_db(database: AuditDb) -> SqliteDatabase:
    """The SAME file the writer opens, read-only — one path, both directions."""
    return read_only(database)


AuditReaderDb = Annotated[SqliteDatabase, Depends(audit_reader_db)]


@singleton
def diagnostics(database: MainDb, audit_database: AuditReaderDb) -> DiagnosticsRepository:
    return SqliteDiagnosticsRepository(database, audit_database)


Diagnostics = Annotated[DiagnosticsRepository, Depends(diagnostics)]


# --- what is installed on this machine ---------------------------------------


@singleton
def registry() -> HarnessRegistry:
    harnesses = HarnessRegistry()
    for plugin in installed():
        harnesses.register(plugin)
    harnesses.validate()
    return harnesses


Registry = Annotated[HarnessRegistry, Depends(registry)]


@singleton
def terminal_plugin() -> TerminalPlugin:
    """Resolved ONCE. When no terminal is installed the null plugin takes the
    seat, so every service below stays unconditional and "no terminal" reads out
    of the audit as an ordinary failure reason."""
    return resolve_terminal() or null_plugin()


InstalledTerminal = Annotated[TerminalPlugin, Depends(terminal_plugin)]


@singleton
def repositories() -> RepositoryQueries:
    return RepositoryQueries()


Repositories = Annotated[RepositoryQueries, Depends(repositories)]


# --- storage, as Protocols: no consumer names an implementation ---------------


@singleton
def canonical_events(database: MainDb) -> CanonicalEventRepository:
    return SqliteCanonicalEventRepository(database)


CanonicalEvents = Annotated[CanonicalEventRepository, Depends(canonical_events)]


@singleton
def raw_events(database: MainDb) -> RawEventRepository:
    return SqliteRawEventRepository(database)


RawEvents = Annotated[RawEventRepository, Depends(raw_events)]


@singleton
def shell_output(database: MainDb) -> ShellOutputRepository:
    return SqliteShellOutputRepository(database)


ShellOutput = Annotated[ShellOutputRepository, Depends(shell_output)]


@singleton
def session_data(database: MainDb) -> SessionDataRepository:
    return SqliteSessionDataRepository(database)


SessionDataStore = Annotated[SessionDataRepository, Depends(session_data)]


@singleton
def workspaces(database: MainDb) -> SessionWorkspaceRepository:
    return SqliteSessionWorkspaceRepository(database)


Workspaces = Annotated[SessionWorkspaceRepository, Depends(workspaces)]


@singleton
def view_modes(database: MainDb) -> ViewModeRepository:
    return SqliteViewModeRepository(database)


ViewModes = Annotated[ViewModeRepository, Depends(view_modes)]


@singleton
def notification_settings(database: MainDb) -> NotificationSettingRepository:
    return SqliteNotificationSettingRepository(database)


NotificationSettings = Annotated[NotificationSettingRepository, Depends(notification_settings)]


@singleton
def hidden_directories(database: MainDb) -> HiddenDirectoryRepository:
    return SqliteHiddenDirectoryRepository(database)


HiddenDirectories = Annotated[HiddenDirectoryRepository, Depends(hidden_directories)]


@singleton
def new_sessions(database: MainDb) -> NewSessionRepository:
    return SqliteNewSessionRepository(database)


NewSessions = Annotated[NewSessionRepository, Depends(new_sessions)]


@singleton
def dismissals(database: MainDb) -> TaskDismissalRepository:
    return SqliteTaskDismissalRepository(database)


Dismissals = Annotated[TaskDismissalRepository, Depends(dismissals)]


@singleton
def push_subscriptions(database: MainDb) -> PushSubscriptionRepository:
    return SqlitePushSubscriptionRepository(database)


PushSubscriptions = Annotated[PushSubscriptionRepository, Depends(push_subscriptions)]


@singleton
def push_signing_keys(database: MainDb) -> PushSigningKeyRepository:
    return SqlitePushSigningKeyRepository(database)


PushSigningKeys = Annotated[PushSigningKeyRepository, Depends(push_signing_keys)]


@singleton
def pane_width_storage(database: MainDb) -> PaneWidthRepository:
    return SqlitePaneWidthRepository(database)


PaneWidthStorage = Annotated[PaneWidthRepository, Depends(pane_width_storage)]


@singleton
def account_usage(database: MainDb) -> AccountUsageRepository:
    return SqliteAccountUsageRepository(database)


AccountUsage = Annotated[AccountUsageRepository, Depends(account_usage)]


@singleton
def naming_jobs(database: MainDb) -> NamingJobRepository:
    return SqliteNamingJobRepository(database)


NamingJobs = Annotated[NamingJobRepository, Depends(naming_jobs)]


@singleton
def upload_storage(database: MainDb) -> UploadRepository:
    return SqliteUploadRepository(database)


UploadStorage = Annotated[UploadRepository, Depends(upload_storage)]


@singleton
def audit_writes(database: AuditDb) -> AuditWriteRepository:
    return SqliteAuditWriteRepository(database)


AuditWrites = Annotated[AuditWriteRepository, Depends(audit_writes)]


@singleton
def recorder(writes: AuditWrites) -> AuditRecorder:
    """What the machinery did. Everything with a constructor takes this; the
    floor (audit/record.py) is for the writers that have no graph."""
    return AuditRecorder(writes)


Recorder = Annotated[AuditRecorder, Depends(recorder)]


@singleton
def audit_reads(database: AuditReaderDb) -> AuditReadRepository:
    return SqliteAuditReadRepository(database)


AuditReads = Annotated[AuditReadRepository, Depends(audit_reads)]


@singleton
def sessions(database: MainDb, harnesses: Registry) -> SessionRepository:
    return SqliteSessionRepository(database, harnesses)


Sessions = Annotated[SessionRepository, Depends(sessions)]


# --- the graph ----------------------------------------------------------------


@singleton
def terminal(plugin: InstalledTerminal, session_storage: Sessions) -> TerminalAdapter:
    return TerminalAdapter(plugin, session_storage)


Terminal = Annotated[TerminalAdapter, Depends(terminal)]


@singleton
def pane_width_service(widths: PaneWidthStorage) -> PaneWidthService:
    return PaneWidthService(widths)


PaneWidths = Annotated[PaneWidthService, Depends(pane_width_service)]


@singleton
def interrupt_registry() -> InterruptRegistry:
    return InterruptRegistry()


InterruptTracking = Annotated[InterruptRegistry, Depends(interrupt_registry)]


@singleton
def control_effects(
    raw: RawEvents,
    workspaces: Workspaces,
    read_model: SessionDataStore,
) -> ControlEffectRecorder:
    return ControlEffectRecorder(raw, workspaces, read_model)


ControlEffects = Annotated[ControlEffectRecorder, Depends(control_effects)]


@singleton
def catalog(harnesses: Registry) -> HarnessCatalogService:
    return HarnessCatalogService(harnesses)


Catalog = Annotated[HarnessCatalogService, Depends(catalog)]


@singleton
def usage_state(harnesses: Registry, usage: AccountUsage) -> ApplicationUsageState:
    return ApplicationUsageState(HarnessUsageService(harnesses, usage))


UsageState = Annotated[ApplicationUsageState, Depends(usage_state)]


@singleton
def model_terminal() -> TerminalPlugin:
    """A private headless terminal whose windows can never become sessions."""
    return pty_plugin()


ModelTerminal = Annotated[TerminalPlugin, Depends(model_terminal)]


@singleton
def model_factory(terminal: ModelTerminal, usage: UsageState) -> ModelFactory:
    return DefaultModelFactory(terminal, usage)


InferenceModels = Annotated[ModelFactory, Depends(model_factory)]


@singleton
def automatic_namer(
    models: InferenceModels,
    jobs: NamingJobs,
    raw: RawEvents,
    read_model: SessionDataStore,
    audit: Recorder,
) -> AutomaticSessionNamer:
    return AutomaticSessionNamer(models, jobs, raw, read_model, audit)


AutomaticNamer = Annotated[AutomaticSessionNamer, Depends(automatic_namer)]


@singleton
def naming_worker(
    jobs: NamingJobs,
    session_storage: Sessions,
    namer: AutomaticNamer,
    audit: Recorder,
) -> NamingJobWorker:
    return NamingJobWorker(jobs, session_storage, namer, audit)


@singleton
def controls(
    session_storage: Sessions,
    adapter: Terminal,
    plugin: InstalledTerminal,
    read_model: SessionDataStore,
    audit: Recorder,
    interrupts: InterruptTracking,
    effects: ControlEffects,
    namer: AutomaticNamer,
) -> HarnessControlService:
    return HarnessControlService(
        session_storage,
        adapter,
        plugin,
        read_model,
        audit,
        interrupts,
        effects,
        namer,
    )


Controls = Annotated[HarnessControlService, Depends(controls)]


@singleton
def terminal_input(session_storage: Sessions, adapter: Terminal, plugin: InstalledTerminal) -> TerminalInputService:
    return TerminalInputService(session_storage, adapter, plugin.viewport)


TerminalInput = Annotated[TerminalInputService, Depends(terminal_input)]


@singleton
def presence() -> Presence:
    """The live presence signals: what the alert path consults to decide whether
    you need telling at all. One per application, and the request threads and the
    notifier thread share it — which is what makes a beat a fact about THIS
    daemon rather than about this interpreter."""
    return Presence()


PresenceSignals = Annotated[Presence, Depends(presence)]


@singleton
def dashboard_notification_state() -> DashboardNotificationState:
    return DashboardNotificationState()


NotificationState = Annotated[DashboardNotificationState, Depends(dashboard_notification_state)]


@singleton
def hook_gateway(harnesses: Registry, raw: RawEvents) -> HookGatewayService:
    return HookGatewayService(harnesses, raw)


HookGateway = Annotated[HookGatewayService, Depends(hook_gateway)]


@singleton
def telemetry_gateway(
    harnesses: Registry, raw: RawEvents, session_storage: Sessions, usage: AccountUsage
) -> TelemetryGatewayService:
    return TelemetryGatewayService(harnesses, raw, session_storage, usage)


TelemetryGateway = Annotated[TelemetryGatewayService, Depends(telemetry_gateway)]


@singleton
def application_preferences(
    read_model: SessionDataStore,
    session_storage: Sessions,
    adapter: Terminal,
    checkouts: Repositories,
    usage: UsageState,
    notices: NotificationState,
    drafts: NewSessions,
    settings: NotificationSettings,
    directories: HiddenDirectories,
    subscriptions: PushSubscriptions,
    signals: PresenceSignals,
) -> ApplicationPreferenceService:
    return ApplicationPreferenceService(
        read_model,
        session_storage,
        adapter,
        checkouts,
        usage,
        notices,
        drafts,
        settings,
        directories,
        subscriptions,
        signals,
    )


ApplicationPreferences = Annotated[ApplicationPreferenceService, Depends(application_preferences)]


@singleton
def session_application(
    read_model: SessionDataStore,
    terminal_input: TerminalInput,
    audit_reader: AuditReads,
    workspace_storage: Workspaces,
    modes: ViewModes,
    settings: NotificationSettings,
    hidden_tasks: Dismissals,
) -> SessionApplicationService:
    return SessionApplicationService(
        read_model,
        terminal_input,
        audit_reader,
        workspace_storage,
        modes,
        settings,
        hidden_tasks,
    )


SessionApplication = Annotated[SessionApplicationService, Depends(session_application)]


@singleton
def launch_effects(
    raw: RawEvents,
    session_storage: Sessions,
) -> SessionLaunchEffectRecorder:
    return SessionLaunchEffectRecorder(raw, session_storage)


LaunchEffects = Annotated[SessionLaunchEffectRecorder, Depends(launch_effects)]


def harness_launch_environment() -> tuple[tuple[str, str], ...]:
    """Values that a terminal-launched harness must get from Baqylau.

    A terminal application is a separate process. It does not inherit changes
    from the daemon environment. Pass only the values that define the harness
    runtime and the callback endpoint.
    """
    return (
        (
            "BAQYLAU_DASHBOARD_PORT",
            os.environ.get("BAQYLAU_DASHBOARD_PORT", "8377"),
        ),
    )


@singleton
def launcher(
    harnesses: Registry,
    adapter: Terminal,
    plugin: InstalledTerminal,
    effects: LaunchEffects,
) -> HarnessLauncherService:
    return HarnessLauncherService(
        harnesses,
        adapter,
        plugin.tabs,
        effects,
        launch_environment=harness_launch_environment(),
    )


Launcher = Annotated[HarnessLauncherService, Depends(launcher)]


@singleton
def pane_commands(adapter: Terminal, widths: PaneWidths, audit: Recorder) -> PaneCommandService:
    return PaneCommandService(adapter, widths, audit)


PaneCommands = Annotated[PaneCommandService, Depends(pane_commands)]


@singleton
def core_translators() -> Mapping[str, CoreTranslator]:
    return {
        AUTOMATIC_TITLE_SOURCE_TYPE: AutomaticTitleTranslator(),
        CONTROL_SOURCE_TYPE: ControlTranslator(),
        OUTPUT_LOCATION_SOURCE_TYPE: ShellOutputTranslator(),
        LIVENESS_SOURCE_TYPE: LivenessTranslator(),
        RESUME_SOURCE_TYPE: SessionResumeTranslator(),
        RESUME_LIVENESS_SOURCE_TYPE: ResumeLivenessTranslator(),
        INTERRUPT_SOURCE_TYPE: InterruptTranslator(),
    }


CoreTranslators = Annotated[Mapping[str, CoreTranslator], Depends(core_translators)]


@singleton
def translation_inputs(
    session_storage: Sessions,
    output: ShellOutput,
    raw: RawEvents,
    repositories: Repositories,
) -> tuple[CanonicalEventReaction, ...]:
    """The two facts the interpreter's own next pull depends on.

    Not reactions in the sense the reaction loop means: the pull phase READS the
    rows these write — `sessions.watchable()` and the follow list — so they have
    to be current before the next tick, on the interpreter's own thread. The
    output following also unlinks the files that phase reads, which one thread
    keeps safe.
    """
    return (
        SessionUpsertCanonicalEventReaction(session_storage, repositories),
        ShellOutputCanonicalEventReaction(output, raw),
    )


TranslationInputs = Annotated[tuple[CanonicalEventReaction, ...], Depends(translation_inputs)]


@singleton
def reactions(
    session_storage: Sessions,
    adapter: Terminal,
    widths: PaneWidths,
    interrupts: InterruptTracking,
    workspaces: Workspaces,
    harnesses: Registry,
    jobs: NamingJobs,
) -> tuple[CanonicalEventReaction, ...]:
    """What a committed fact CAUSES, in dependency order, on the reaction loop."""
    return (
        AutomaticNamingReaction(harnesses, jobs),
        PaneCanonicalEventReaction(adapter, session_storage, widths),
        QueuedPromptCanonicalEventReaction(workspaces),
        InterruptCanonicalEventReaction(interrupts),
    )


Reactions = Annotated[tuple[CanonicalEventReaction, ...], Depends(reactions)]


@singleton
def model_naming(harness_registry: Registry) -> ModelNaming:
    """One namer per harness, for the writers: the reason a model shows the
    SAME name in the picker, on the actor row and in the feed."""
    return ModelNaming(
        {
            plugin.info.name: plugin.model_display
            for plugin in harness_registry.plugins()
            if plugin.model_display is not None
        }
    )


Naming = Annotated[ModelNaming, Depends(model_naming)]


@singleton
def entry_writer(naming: Naming) -> SessionEntryWriter:
    return EntryWriter(naming)


EntryWrites = Annotated[SessionEntryWriter, Depends(entry_writer)]


@singleton
def session_data_writers(naming: Naming) -> tuple[SessionDataWriter, ...]:
    """The aggregate's writers, in the order they fold.

    Order matters in exactly one place: `ActorWriter` is the only one that
    creates an actor row, and the four after it only ever update one that
    exists — so it goes first, and a fact about an actor's usage in the same
    event as its birth still lands.
    """
    return (
        SessionWriter(),
        GoalWriter(),
        TaskWriter(),
        ActorWriter(naming),
        StatusWriter(),
        UsageWriter(),
        ContextWriter(),
        StatisticsWriter(),
    )


SessionDataWriters = Annotated[tuple[SessionDataWriter, ...], Depends(session_data_writers)]


@singleton
def interpreter(
    session_storage: Sessions,
    harnesses: Registry,
    raw: RawEvents,
    output: ShellOutput,
    events: CanonicalEvents,
    translators: CoreTranslators,
    inputs: TranslationInputs,
    audit: Recorder,
    interrupts: InterruptTracking,
    adapter: Terminal,
    effects: LaunchEffects,
) -> Interpreter:
    return Interpreter(
        session_storage,
        harnesses,
        raw,
        output,
        events,
        translators,
        inputs,
        audit,
        interrupts,
        session_terminal_state=adapter,
        session_resume_recorder=effects,
    )


@singleton
def applied_listeners(adapter: Terminal, session_storage: Sessions) -> tuple[AppliedActorListener, ...]:
    """What a COMMITTED aggregate change causes, as opposed to what a fact does.

    Wired here rather than inside the loop for the same reason the pane reaction
    is: the engine drives a terminal it is handed and may not name one.
    """
    return (TabColorPainter(adapter, session_storage),)


AppliedListeners = Annotated[tuple[AppliedActorListener, ...], Depends(applied_listeners)]


@singleton
def reaction_loop(
    events: CanonicalEvents,
    read_model: SessionDataStore,
    event_reactions: Reactions,
    entries: EntryWrites,
    writers: SessionDataWriters,
    listeners: AppliedListeners,
    harnesses: Registry,
    control_service: Controls,
    audit: Recorder,
) -> ReactionLoop:
    return ReactionLoop(
        events,
        read_model,
        event_reactions,
        entries,
        writers,
        listeners,
        harnesses,
        control_service,
        audit,
    )


@singleton
def insights(
    read_model: SessionDataStore,
    terminal_input: TerminalInput,
    audit_reader: AuditReads,
    checkouts: Repositories,
) -> ApplicationInsightsService:
    return ApplicationInsightsService(
        read_model,
        terminal_input,
        audit_reader,
        checkouts,
        top_project_count=dashboard_config.INSIGHTS_PROJECT_LIMIT,
    )


Insights = Annotated[ApplicationInsightsService, Depends(insights)]


@singleton
def resumable_sessions(
    read_model: SessionDataStore,
    terminal_input: TerminalInput,
    checkouts: Repositories,
) -> ResumableSessionService:
    return ResumableSessionService(
        read_model,
        terminal_input,
        checkouts,
        result_limit=dashboard_config.RESUMABLE_SESSION_LIMIT,
    )


ResumableSessions = Annotated[ResumableSessionService, Depends(resumable_sessions)]


@singleton
def uploads(storage: UploadStorage) -> UploadService:
    return UploadService(storage)


Uploads = Annotated[UploadService, Depends(uploads)]


@singleton
def browser_telemetry(recorder: AuditWrites) -> BrowserTelemetryService:
    return BrowserTelemetryService(recorder, os.getpid())


BrowserTelemetry = Annotated[BrowserTelemetryService, Depends(browser_telemetry)]
