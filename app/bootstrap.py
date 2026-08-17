"""Build the canonical application graph with installed concrete harnesses.

The one place that knows WHICH harnesses and which terminal are installed — and
now the one place that opens a database. Three `SqliteDatabase` handles are
built here and initialised once each; every repository below takes one, and
every service takes the repositories it needs as constructor parameters.
Nothing further down resolves a path, opens a connection, or manages a
transaction.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from dashboard import config as dashboard_config

from harness.contract import CanonicalEventReaction, CoreTranslator
from harness.models import LIVENESS_SOURCE_TYPE, OUTPUT_LOCATION_SOURCE_TYPE
from core import data
from engine.queries.content import CanonicalContentService
from engine.interpret.translators import LivenessTranslator, OperationOutputTranslator
from harness.hooks.gateway import HookGatewayService
from harness.services.telemetry import TelemetryGatewayService
from app.services.insights import ApplicationInsightsService
from engine.interpret.loop import Interpreter
from terminal.panes.commands import PaneCommandService
from terminal.panes.streams import PaneStreamService
from terminal.services.panes import PaneWidthService
from terminal.services.views import ContentViewService
from harness.impl import installed
from engine.interpret.reactions import (
    OperationOutputCanonicalEventReaction,
    SessionUpsertCanonicalEventReaction,
)
from terminal.panes.reaction import PaneCanonicalEventReaction
from core.repository import RepositoryQueries
from app.services.resume import ResumableSessionService
from app.services.uploads import UploadService
from diagnostics.telemetry import BrowserTelemetryService
from harness.services.catalog import HarnessCatalogService
from harness.services.controls import HarnessControlService
from harness.services.launcher import HarnessLauncherService
from harness.services.probe import TerminalInputService
from harness.services.usage import ApplicationUsageState, HarnessUsageService
from dashboard.services.activity import DashboardActivityService
from dashboard.services.notices import DashboardNotificationState
from dashboard.services.overview import GlobalApplicationService
from dashboard.services.sessions import DashboardSessionService
from dashboard.services.streams import DashboardStreamService
from dashboard.services.workspace import SessionApplicationService
from harness.registry import HarnessRegistry
from engine.projections import SessionQueries
from terminal.adapter import TerminalAdapter
from terminal.impl import resolve as resolve_terminal
from terminal.impl.null import null_plugin
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
    NotificationSettingRepository,
    PushSigningKeyRepository,
    PushSubscriptionRepository,
)
from repository.contract.sessions import SessionRepository
from repository.contract.usage import AccountUsageRepository
from repository.impl.sqlite.canonical_events import SqliteCanonicalEventRepository
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


@dataclass(frozen=True)
class CanonicalApplication:
    # --- storage, as Protocols: no consumer names an implementation ---
    canonical_events: CanonicalEventRepository
    raw_events: RawEventRepository
    sessions: SessionRepository
    evidence: TranslationEvidenceRepository
    operation_output: OperationOutputRepository
    diagnostics: DiagnosticReadRepository
    audit: DiagnosticWriteRepository
    account_usage: AccountUsageRepository
    notification_settings: NotificationSettingRepository
    push_subscriptions: PushSubscriptionRepository
    push_signing_keys: PushSigningKeyRepository
    # --- the rest of the graph ---
    registry: HarnessRegistry
    hook_gateway: HookGatewayService
    telemetry_gateway: TelemetryGatewayService
    queries: SessionQueries
    dashboard_activity: DashboardActivityService
    dashboard_sessions: DashboardSessionService
    dashboard_stream: DashboardStreamService
    content: CanonicalContentService
    dashboard_notification_state: DashboardNotificationState
    usage_state: ApplicationUsageState
    global_application: GlobalApplicationService
    session_application: SessionApplicationService
    controls: HarnessControlService
    launcher: HarnessLauncherService
    catalog: HarnessCatalogService
    terminal_input: TerminalInputService
    terminal: TerminalAdapter
    pane_commands: PaneCommandService
    pane_streams: PaneStreamService
    content_views: ContentViewService
    interpreter: Interpreter
    insights: ApplicationInsightsService
    resumable_sessions: ResumableSessionService
    uploads: UploadService
    browser_telemetry: BrowserTelemetryService


def default_data_directory() -> str:
    return data.data_directory()


def build_application(
    data_directory: str,
    audit_database_path: str | None = None,
) -> CanonicalApplication:
    directory = os.path.abspath(data_directory)
    # Two files, two handles, one initialize each. Four separate store objects
    # used to apply the same schema to the same file four times at startup.
    main = main_database(os.path.join(directory, data.MAIN_DATABASE_NAME))
    audit = audit_database(
        audit_database_path or os.path.join(directory, data.AUDIT_DATABASE_NAME)
    )

    canonical_events = SqliteCanonicalEventRepository(main)
    raw_events = SqliteRawEventRepository(main)
    operation_output = SqliteOperationOutputRepository(main)
    evidence = SqliteTranslationEvidenceRepository(main)
    workspaces = SqliteSessionWorkspaceRepository(main)
    view_modes = SqliteViewModeRepository(main)
    notification_settings = SqliteNotificationSettingRepository(main)
    hidden_directories = SqliteHiddenDirectoryRepository(main)
    new_sessions = SqliteNewSessionRepository(main)
    dismissals = SqliteTaskDismissalRepository(main)
    push_subscriptions = SqlitePushSubscriptionRepository(main)
    push_signing_keys = SqlitePushSigningKeyRepository(main)
    pane_widths = SqlitePaneWidthRepository(main)
    view_repository = SqliteContentViewRepository(main)
    account_usage = SqliteAccountUsageRepository(main)
    upload_repository = SqliteUploadRepository(main)
    # The reader opens the SAME file the writer opens, read-only. They used to
    # be two independently configured paths, which in the test graph pointed at
    # two different files.
    audit_writes = SqliteDiagnosticWriteRepository(audit)
    diagnostics = SqliteDiagnosticReadRepository(read_only(audit))

    repositories = RepositoryQueries()
    registry = HarnessRegistry()
    for plugin in installed():
        registry.register(plugin)
    registry.validate()
    sessions = SqliteSessionRepository(main, registry)

    # The terminal is resolved ONCE, here, and passed down as fields: a
    # consumer holds the sub-protocol it needs, never the resolver. When no
    # terminal is installed the null plugin takes the seat, so every service
    # below stays unconditional and "no terminal" reads out of the audit as an
    # ordinary failure reason.
    terminal_plugin = resolve_terminal() or null_plugin()
    terminal = TerminalAdapter(terminal_plugin, sessions)
    pane_width_service = PaneWidthService(pane_widths)
    content_views = ContentViewService(view_repository, audit_writes)
    queries = SessionQueries(canonical_events, sessions)
    controls = HarnessControlService(
        sessions, terminal, terminal_plugin, queries, account_usage
    )
    catalog = HarnessCatalogService(registry)
    usage_state = ApplicationUsageState(HarnessUsageService(registry, account_usage))
    terminal_input = TerminalInputService(sessions, terminal, terminal_plugin.viewport)
    dashboard_sessions = DashboardSessionService(
        canonical_events, queries, terminal_input, repositories
    )
    dashboard_notification_state = DashboardNotificationState()
    content = CanonicalContentService(canonical_events, queries)
    core_translators: Mapping[str, CoreTranslator] = {
        OUTPUT_LOCATION_SOURCE_TYPE: OperationOutputTranslator(),
        LIVENESS_SOURCE_TYPE: LivenessTranslator(),
    }
    reactions: tuple[CanonicalEventReaction, ...] = (
        # The sessions row exists and is current before the panes anchor to it.
        SessionUpsertCanonicalEventReaction(sessions),
        OperationOutputCanonicalEventReaction(operation_output, raw_events),
        PaneCanonicalEventReaction(terminal, sessions, pane_width_service),
    )
    return CanonicalApplication(
        canonical_events=canonical_events,
        raw_events=raw_events,
        sessions=sessions,
        evidence=evidence,
        operation_output=operation_output,
        diagnostics=diagnostics,
        audit=audit_writes,
        account_usage=account_usage,
        notification_settings=notification_settings,
        push_subscriptions=push_subscriptions,
        push_signing_keys=push_signing_keys,
        registry=registry,
        hook_gateway=HookGatewayService(registry, raw_events),
        telemetry_gateway=TelemetryGatewayService(
            registry, raw_events, sessions, account_usage
        ),
        queries=queries,
        dashboard_activity=DashboardActivityService(canonical_events, queries),
        dashboard_sessions=dashboard_sessions,
        dashboard_stream=DashboardStreamService(
            canonical_events, queries, terminal_input, repositories
        ),
        content=content,
        dashboard_notification_state=dashboard_notification_state,
        usage_state=usage_state,
        global_application=GlobalApplicationService(
            dashboard_sessions,
            usage_state,
            dashboard_notification_state,
            new_sessions,
            notification_settings,
            hidden_directories,
            push_subscriptions,
        ),
        session_application=SessionApplicationService(
            canonical_events,
            queries,
            terminal_input,
            diagnostics,
            workspaces,
            view_modes,
            notification_settings,
            dismissals,
        ),
        controls=controls,
        launcher=HarnessLauncherService(registry, terminal, terminal_plugin.tabs),
        catalog=catalog,
        terminal_input=terminal_input,
        terminal=terminal,
        pane_commands=PaneCommandService(terminal, pane_width_service),
        pane_streams=PaneStreamService(
            canonical_events,
            queries,
            sessions,
            content,
            terminal,
            content_views,
        ),
        content_views=content_views,
        interpreter=Interpreter(
            sessions,
            registry,
            raw_events,
            operation_output,
            canonical_events,
            core_translators,
            reactions,
            controls,
        ),
        insights=ApplicationInsightsService(
            canonical_events,
            queries,
            terminal_input,
            diagnostics,
            repositories,
            top_project_count=dashboard_config.INSIGHTS_PROJECT_LIMIT,
        ),
        resumable_sessions=ResumableSessionService(
            canonical_events,
            queries,
            terminal_input,
            repositories,
            result_limit=dashboard_config.RESUMABLE_SESSION_LIMIT,
        ),
        uploads=UploadService(upload_repository),
        browser_telemetry=BrowserTelemetryService(audit_writes, os.getpid()),
    )


def build_default_application() -> CanonicalApplication:
    """The daemon's graph. Its audit writer addresses the same file the
    out-of-daemon facade does — one path, both directions — but the graph holds
    its own handle so every daemon-side write is injected."""
    return build_application(default_data_directory())
