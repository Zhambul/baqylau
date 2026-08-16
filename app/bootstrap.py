"""Build the canonical application graph with installed concrete harnesses."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from dashboard import config as dashboard_config

from contracts.harness import (
    CanonicalEventReaction,
    CoreTranslator,
    LIVENESS_SOURCE_TYPE,
    OUTPUT_LOCATION_SOURCE_TYPE,
)
from app.data import data_directory
from app.content import CanonicalContentService
from app.core_translators import LivenessTranslator, OperationOutputTranslator
from app.diagnostics import OperationalDiagnostics
from app.hook_gateway import HookGatewayService
from app.insights import ApplicationInsightsService
from app.interpreter import Interpreter
from app.pane_commands import PaneCommandService
from app.pane_streams import PaneStreamService
from app.plugins import installed_plugins
from app.reactions import (
    OperationOutputCanonicalEventReaction,
    PaneCanonicalEventReaction,
    SessionUpsertCanonicalEventReaction,
)
from app.repository import RepositoryQueries
from app.resume import ResumableSessionService
from app.session_terminal import ApplicationTerminal
from app.telemetry import BrowserTelemetryService
from app.services import (
    HarnessCatalogService,
    HarnessControlService,
    HarnessLauncherService,
    HarnessUsageService,
    TerminalInputService,
)
from app.usage import ApplicationUsageState
from dashboard.activity import (
    DashboardActivityService,
    DashboardSessionService,
    DashboardStreamService,
)
from dashboard.application import (
    DashboardNotificationState,
    GlobalApplicationService,
    SessionApplicationService,
)
from runtime.canonical_store import CanonicalEventStore
from runtime.evidence import EvidenceQueries
from runtime.harnesses import HarnessRegistry
from runtime.operation_output import OperationOutputStore
from runtime.projections import SessionQueries
from runtime.recorder import RawEventRecorder
from runtime.sessions import SessionStore


@dataclass(frozen=True)
class CanonicalApplication:
    canonical_store: CanonicalEventStore
    registry: HarnessRegistry
    sessions: SessionStore
    recorder: RawEventRecorder
    hook_gateway: HookGatewayService
    operation_output: OperationOutputStore
    queries: SessionQueries
    evidence: EvidenceQueries
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
    terminal: ApplicationTerminal
    pane_commands: PaneCommandService
    pane_streams: PaneStreamService
    interpreter: Interpreter
    diagnostics: OperationalDiagnostics
    insights: ApplicationInsightsService
    resumable_sessions: ResumableSessionService
    browser_telemetry: BrowserTelemetryService


def default_data_directory() -> str:
    return data_directory()


def build_application(
    data_directory: str,
    diagnostic_database_path: str | None = None,
) -> CanonicalApplication:
    database_path = os.path.join(os.path.abspath(data_directory), "events.db")
    canonical_store = CanonicalEventStore(database_path)
    recorder = RawEventRecorder(database_path)
    operation_output = OperationOutputStore(database_path)
    diagnostics = OperationalDiagnostics(
        diagnostic_database_path
        or os.path.join(os.path.abspath(data_directory), "diagnostics.db")
    )
    repositories = RepositoryQueries()
    registry = HarnessRegistry()
    for plugin in installed_plugins():
        registry.register(plugin)
    registry.validate()
    sessions = SessionStore(database_path, registry)
    terminal = ApplicationTerminal()
    queries = SessionQueries(canonical_store, sessions)
    controls = HarnessControlService(sessions, terminal, queries)
    catalog = HarnessCatalogService(registry)
    usage_state = ApplicationUsageState(HarnessUsageService(registry))
    terminal_input = TerminalInputService(sessions, terminal, terminal)
    dashboard_sessions = DashboardSessionService(
        canonical_store, queries, terminal_input, repositories
    )
    dashboard_notification_state = DashboardNotificationState()
    content = CanonicalContentService(canonical_store, queries)
    core_translators: Mapping[str, CoreTranslator] = {
        OUTPUT_LOCATION_SOURCE_TYPE: OperationOutputTranslator(),
        LIVENESS_SOURCE_TYPE: LivenessTranslator(),
    }
    reactions: tuple[CanonicalEventReaction, ...] = (
        # The sessions row exists and is current before the panes anchor to it.
        SessionUpsertCanonicalEventReaction(sessions),
        OperationOutputCanonicalEventReaction(operation_output, recorder),
        PaneCanonicalEventReaction(terminal, sessions),
    )
    from core import audit

    browser_telemetry = BrowserTelemetryService(audit)
    return CanonicalApplication(
        canonical_store=canonical_store,
        registry=registry,
        sessions=sessions,
        recorder=recorder,
        hook_gateway=HookGatewayService(registry, recorder),
        operation_output=operation_output,
        queries=queries,
        evidence=EvidenceQueries(canonical_store),
        dashboard_activity=DashboardActivityService(canonical_store, queries),
        dashboard_sessions=dashboard_sessions,
        dashboard_stream=DashboardStreamService(
            canonical_store, queries, terminal_input, repositories
        ),
        content=content,
        dashboard_notification_state=dashboard_notification_state,
        usage_state=usage_state,
        global_application=GlobalApplicationService(
            dashboard_sessions,
            usage_state,
            dashboard_notification_state,
        ),
        session_application=SessionApplicationService(
            canonical_store,
            queries,
            terminal_input,
            diagnostics,
        ),
        controls=controls,
        launcher=HarnessLauncherService(registry, terminal),
        catalog=catalog,
        terminal_input=terminal_input,
        terminal=terminal,
        pane_commands=PaneCommandService(terminal),
        pane_streams=PaneStreamService(
            canonical_store,
            queries,
            sessions,
            content,
            terminal,
        ),
        interpreter=Interpreter(
            sessions,
            registry,
            recorder,
            operation_output,
            canonical_store,
            core_translators,
            reactions,
            controls,
        ),
        diagnostics=diagnostics,
        insights=ApplicationInsightsService(
            canonical_store,
            queries,
            terminal_input,
            diagnostics,
            repositories,
            top_project_count=dashboard_config.INSIGHTS_PROJECT_LIMIT,
        ),
        resumable_sessions=ResumableSessionService(
            canonical_store,
            queries,
            terminal_input,
            repositories,
            result_limit=dashboard_config.RESUMABLE_SESSION_LIMIT,
        ),
        browser_telemetry=browser_telemetry,
    )


def build_default_application() -> CanonicalApplication:
    from core.audit import db_path as diagnostic_database_path

    return build_application(default_data_directory(), diagnostic_database_path())
