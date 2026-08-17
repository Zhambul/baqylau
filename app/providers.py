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
from dashboard.services.activity import DashboardActivityService
from dashboard.services.notices import DashboardNotificationState
from dashboard.services.overview import GlobalApplicationService
from dashboard.services.sessions import DashboardSessionService
from dashboard.services.streams import DashboardStreamService
from dashboard.services.workspace import SessionApplicationService
from diagnostics.recorder import AuditRecorder
from diagnostics.telemetry import BrowserTelemetryService
from engine.interpret.loop import Interpreter
from engine.interpret.reactions import (
    OperationOutputCanonicalEventReaction,
    SessionUpsertCanonicalEventReaction,
)
from engine.interpret.translators import LivenessTranslator, OperationOutputTranslator
from engine.projections import SessionQueries
from engine.queries.content import CanonicalContentService
from harness.contract import CanonicalEventReaction, CoreTranslator
from harness.hooks.gateway import HookGatewayService
from harness.impl import installed
from harness.models import LIVENESS_SOURCE_TYPE, OUTPUT_LOCATION_SOURCE_TYPE
from harness.registry import HarnessRegistry
from harness.services.catalog import HarnessCatalogService
from harness.services.controls import HarnessControlService
from harness.services.launcher import HarnessLauncherService
from harness.services.probe import TerminalInputService
from harness.services.telemetry import TelemetryGatewayService
from harness.services.usage import ApplicationUsageState, HarnessUsageService
from notify.presence import Presence
from repository.contract.diagnostics import (
    DiagnosticReadRepository,
    DiagnosticWriteRepository,
)
from repository.contract.facts import (
    CanonicalEventRepository,
    RawEventRepository,
    TranslationEvidenceRepository,
)
from repository.contract.operations import OperationOutputRepository
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
from repository.contract.terminal import ContentViewRepository, PaneWidthRepository
from repository.contract.uploads import UploadRepository
from repository.contract.usage import AccountUsageRepository
from repository.contract.workspace import SessionWorkspaceRepository
from repository.impl.sqlite.canonical_events import SqliteCanonicalEventRepository
from repository.impl.sqlite.connection import SqliteDatabase
from repository.impl.sqlite.databases import audit_database, main_database, read_only
from repository.impl.sqlite.diagnostics import (
    SqliteDiagnosticReadRepository,
    SqliteDiagnosticWriteRepository,
)
from repository.impl.sqlite.evidence import SqliteTranslationEvidenceRepository
from repository.impl.sqlite.operation_output import SqliteOperationOutputRepository
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
    SqliteContentViewRepository,
    SqlitePaneWidthRepository,
)
from repository.impl.sqlite.uploads import SqliteUploadRepository
from repository.impl.sqlite.usage import SqliteAccountUsageRepository
from repository.impl.sqlite.workspace import SqliteSessionWorkspaceRepository
from terminal.adapter import TerminalAdapter
from terminal.contract import TerminalPlugin
from terminal.impl import resolve as resolve_terminal
from terminal.impl.null import null_plugin
from terminal.panes.commands import PaneCommandService
from terminal.panes.reaction import PaneCanonicalEventReaction
from terminal.panes.streams import PaneStreamService
from terminal.services.panes import PaneWidthService
from terminal.services.views import ContentViewService

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
def operation_output(database: MainDb) -> OperationOutputRepository:
    return SqliteOperationOutputRepository(database)


OperationOutput = Annotated[OperationOutputRepository, Depends(operation_output)]


@singleton
def evidence(database: MainDb) -> TranslationEvidenceRepository:
    return SqliteTranslationEvidenceRepository(database)



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
def content_view_storage(database: MainDb) -> ContentViewRepository:
    return SqliteContentViewRepository(database)


ContentViewStorage = Annotated[ContentViewRepository, Depends(content_view_storage)]


@singleton
def account_usage(database: MainDb) -> AccountUsageRepository:
    return SqliteAccountUsageRepository(database)


AccountUsage = Annotated[AccountUsageRepository, Depends(account_usage)]


@singleton
def upload_storage(database: MainDb) -> UploadRepository:
    return SqliteUploadRepository(database)


UploadStorage = Annotated[UploadRepository, Depends(upload_storage)]


@singleton
def audit(database: AuditDb) -> DiagnosticWriteRepository:
    return SqliteDiagnosticWriteRepository(database)


Audit = Annotated[DiagnosticWriteRepository, Depends(audit)]


@singleton
def recorder(writes: Audit) -> AuditRecorder:
    """What the machinery did. Everything with a constructor takes this; the
    floor (diagnostics/record.py) is for the writers that have no graph."""
    return AuditRecorder(writes)


Recorder = Annotated[AuditRecorder, Depends(recorder)]


@singleton
def diagnostics(database: AuditReaderDb) -> DiagnosticReadRepository:
    return SqliteDiagnosticReadRepository(database)


Diagnostics = Annotated[DiagnosticReadRepository, Depends(diagnostics)]


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
def content_views(storage: ContentViewStorage, recorder: Audit) -> ContentViewService:
    return ContentViewService(storage, recorder)


ContentViews = Annotated[ContentViewService, Depends(content_views)]


@singleton
def queries(events: CanonicalEvents, session_storage: Sessions) -> SessionQueries:
    return SessionQueries(events, session_storage)


Queries = Annotated[SessionQueries, Depends(queries)]


@singleton
def controls(
    session_storage: Sessions,
    adapter: Terminal,
    plugin: InstalledTerminal,
    session_queries: Queries,
    usage: AccountUsage,
    audit: Recorder,
) -> HarnessControlService:
    return HarnessControlService(
        session_storage, adapter, plugin, session_queries, usage, audit
    )


Controls = Annotated[HarnessControlService, Depends(controls)]


@singleton
def catalog(harnesses: Registry) -> HarnessCatalogService:
    return HarnessCatalogService(harnesses)


Catalog = Annotated[HarnessCatalogService, Depends(catalog)]


@singleton
def usage_state(harnesses: Registry, usage: AccountUsage) -> ApplicationUsageState:
    return ApplicationUsageState(HarnessUsageService(harnesses, usage))


UsageState = Annotated[ApplicationUsageState, Depends(usage_state)]


@singleton
def terminal_input(
    session_storage: Sessions, adapter: Terminal, plugin: InstalledTerminal
) -> TerminalInputService:
    return TerminalInputService(session_storage, adapter, plugin.viewport)


TerminalInput = Annotated[TerminalInputService, Depends(terminal_input)]


@singleton
def dashboard_sessions(
    events: CanonicalEvents,
    session_queries: Queries,
    reader: TerminalInput,
    checkouts: Repositories,
) -> DashboardSessionService:
    return DashboardSessionService(events, session_queries, reader, checkouts)


DashboardSessions = Annotated[DashboardSessionService, Depends(dashboard_sessions)]


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
def content(events: CanonicalEvents, session_queries: Queries) -> CanonicalContentService:
    return CanonicalContentService(events, session_queries)


Content = Annotated[CanonicalContentService, Depends(content)]


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
def dashboard_activity(events: CanonicalEvents, session_queries: Queries) -> DashboardActivityService:
    return DashboardActivityService(events, session_queries)


DashboardActivity = Annotated[DashboardActivityService, Depends(dashboard_activity)]


@singleton
def dashboard_stream(
    events: CanonicalEvents,
    session_queries: Queries,
    reader: TerminalInput,
    checkouts: Repositories,
) -> DashboardStreamService:
    return DashboardStreamService(events, session_queries, reader, checkouts)


DashboardStream = Annotated[DashboardStreamService, Depends(dashboard_stream)]


@singleton
def global_application(
    listing: DashboardSessions,
    usage: UsageState,
    notices: NotificationState,
    drafts: NewSessions,
    settings: NotificationSettings,
    directories: HiddenDirectories,
    subscriptions: PushSubscriptions,
    signals: PresenceSignals,
) -> GlobalApplicationService:
    return GlobalApplicationService(
        listing, usage, notices, drafts, settings, directories, subscriptions, signals
    )


GlobalApplication = Annotated[GlobalApplicationService, Depends(global_application)]


@singleton
def session_application(
    events: CanonicalEvents,
    session_queries: Queries,
    reader: TerminalInput,
    audit_reader: Diagnostics,
    workspace_storage: Workspaces,
    modes: ViewModes,
    settings: NotificationSettings,
    hidden_tasks: Dismissals,
) -> SessionApplicationService:
    return SessionApplicationService(
        events,
        session_queries,
        reader,
        audit_reader,
        workspace_storage,
        modes,
        settings,
        hidden_tasks,
    )


SessionApplication = Annotated[SessionApplicationService, Depends(session_application)]


@singleton
def launcher(
    harnesses: Registry, adapter: Terminal, plugin: InstalledTerminal
) -> HarnessLauncherService:
    return HarnessLauncherService(harnesses, adapter, plugin.tabs)


Launcher = Annotated[HarnessLauncherService, Depends(launcher)]


@singleton
def pane_commands(adapter: Terminal, widths: PaneWidths, audit: Recorder) -> PaneCommandService:
    return PaneCommandService(adapter, widths, audit)


PaneCommands = Annotated[PaneCommandService, Depends(pane_commands)]


@singleton
def pane_streams(
    events: CanonicalEvents,
    session_queries: Queries,
    session_storage: Sessions,
    canonical_content: Content,
    adapter: Terminal,
    views: ContentViews,
) -> PaneStreamService:
    return PaneStreamService(
        events, session_queries, session_storage, canonical_content, adapter, views
    )


PaneStreams = Annotated[PaneStreamService, Depends(pane_streams)]


@singleton
def core_translators() -> Mapping[str, CoreTranslator]:
    return {
        OUTPUT_LOCATION_SOURCE_TYPE: OperationOutputTranslator(),
        LIVENESS_SOURCE_TYPE: LivenessTranslator(),
    }


CoreTranslators = Annotated[Mapping[str, CoreTranslator], Depends(core_translators)]


@singleton
def reactions(
    session_storage: Sessions,
    output: OperationOutput,
    raw: RawEvents,
    adapter: Terminal,
    widths: PaneWidths,
) -> tuple[CanonicalEventReaction, ...]:
    return (
        # The sessions row exists and is current before the panes anchor to it.
        SessionUpsertCanonicalEventReaction(session_storage),
        OperationOutputCanonicalEventReaction(output, raw),
        PaneCanonicalEventReaction(adapter, session_storage, widths),
    )


Reactions = Annotated[tuple[CanonicalEventReaction, ...], Depends(reactions)]


@singleton
def interpreter(
    session_storage: Sessions,
    harnesses: Registry,
    raw: RawEvents,
    output: OperationOutput,
    events: CanonicalEvents,
    translators: CoreTranslators,
    event_reactions: Reactions,
    control_service: Controls,
    audit: Recorder,
) -> Interpreter:
    return Interpreter(
        session_storage,
        harnesses,
        raw,
        output,
        events,
        translators,
        event_reactions,
        control_service,
        audit,
    )



@singleton
def insights(
    events: CanonicalEvents,
    session_queries: Queries,
    reader: TerminalInput,
    audit_reader: Diagnostics,
    checkouts: Repositories,
) -> ApplicationInsightsService:
    return ApplicationInsightsService(
        events,
        session_queries,
        reader,
        audit_reader,
        checkouts,
        top_project_count=dashboard_config.INSIGHTS_PROJECT_LIMIT,
    )


Insights = Annotated[ApplicationInsightsService, Depends(insights)]


@singleton
def resumable_sessions(
    events: CanonicalEvents,
    session_queries: Queries,
    reader: TerminalInput,
    checkouts: Repositories,
) -> ResumableSessionService:
    return ResumableSessionService(
        events,
        session_queries,
        reader,
        checkouts,
        result_limit=dashboard_config.RESUMABLE_SESSION_LIMIT,
    )


ResumableSessions = Annotated[ResumableSessionService, Depends(resumable_sessions)]


@singleton
def uploads(storage: UploadStorage) -> UploadService:
    return UploadService(storage)


Uploads = Annotated[UploadService, Depends(uploads)]


@singleton
def browser_telemetry(recorder: Audit) -> BrowserTelemetryService:
    return BrowserTelemetryService(recorder, os.getpid())


BrowserTelemetry = Annotated[BrowserTelemetryService, Depends(browser_telemetry)]
