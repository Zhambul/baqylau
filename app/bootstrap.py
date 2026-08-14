"""Build the canonical application graph with installed concrete harnesses."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dashboard import config as dashboard_config

from app.data import data_directory
from app.content import CanonicalContentService
from app.diagnostics import OperationalDiagnostics
from app.delivery import ApplicationEventDelivery, SessionLifecycleService
from app.host import ApplicationHost
from app.insights import ApplicationInsightsService
from app.memory import MemoryService
from app.observe import ObservationRunner
from app.plugins import installed_plugins
from app.repository import RepositoryQueries
from app.resume import ResumableSessionService
from app.session_terminal import ApplicationTerminal
from app.telemetry import BrowserTelemetryService
from app.services import (
    HarnessCatalogService,
    HarnessControlService,
    HarnessHookService,
    HarnessLauncherService,
    HarnessUsageService,
    TerminalInputService,
)
from app.usage import ApplicationUsageState
from contracts.harness import SessionLifecycleContext
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
from dashboard.memory import DashboardMemoryService
from runtime.event_store import EventStore
from runtime.evidence import EvidenceQueries
from runtime.ingest import EventPipeline
from runtime.projections import SessionQueries
from runtime.registry import HarnessRegistry
from runtime.state import SqliteCheckpointStore


@dataclass(frozen=True)
class CanonicalApplication:
    event_store: EventStore
    registry: HarnessRegistry
    delivery: ApplicationEventDelivery
    checkpoints: SqliteCheckpointStore
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
    hooks: HarnessHookService
    launcher: HarnessLauncherService
    catalog: HarnessCatalogService
    terminal_input: TerminalInputService
    terminal: ApplicationTerminal
    observation_runner: ObservationRunner
    diagnostics: OperationalDiagnostics
    insights: ApplicationInsightsService
    resumable_sessions: ResumableSessionService
    browser_telemetry: BrowserTelemetryService
    memory: DashboardMemoryService
    host: ApplicationHost


def default_data_directory() -> str:
    return data_directory()


def build_application(
    data_directory: str,
    diagnostic_database_path: str | None = None,
) -> CanonicalApplication:
    event_store = EventStore(os.path.join(os.path.abspath(data_directory), "events.db"))
    diagnostics = OperationalDiagnostics(
        diagnostic_database_path
        or os.path.join(os.path.abspath(data_directory), "diagnostics.db")
    )
    repositories = RepositoryQueries()
    registry = HarnessRegistry(event_store)
    for plugin in installed_plugins():
        registry.register(plugin)
    registry.validate()
    pipeline = EventPipeline(registry, event_store)
    checkpoints = SqliteCheckpointStore(event_store)
    terminal = ApplicationTerminal()
    queries = SessionQueries(event_store)
    controls = HarnessControlService(registry, terminal, queries)
    catalog = HarnessCatalogService(registry)
    usage_state = ApplicationUsageState(HarnessUsageService(registry))
    terminal_input = TerminalInputService(registry, terminal, terminal)
    dashboard_sessions = DashboardSessionService(
        event_store, queries, terminal_input, repositories
    )
    dashboard_notification_state = DashboardNotificationState()
    memory = MemoryService(registry, queries)
    session_lifecycle = SessionLifecycleService(
        registry,
        SessionLifecycleContext(terminal, terminal),
    )
    delivery = ApplicationEventDelivery(pipeline, event_store, session_lifecycle)
    host = ApplicationHost()
    from core import audit

    browser_telemetry = BrowserTelemetryService(audit)
    return CanonicalApplication(
        event_store=event_store,
        registry=registry,
        delivery=delivery,
        checkpoints=checkpoints,
        queries=queries,
        evidence=EvidenceQueries(event_store),
        dashboard_activity=DashboardActivityService(event_store, queries),
        dashboard_sessions=dashboard_sessions,
        dashboard_stream=DashboardStreamService(
            event_store, queries, terminal_input, repositories
        ),
        content=CanonicalContentService(event_store, queries),
        dashboard_notification_state=dashboard_notification_state,
        usage_state=usage_state,
        global_application=GlobalApplicationService(
            dashboard_sessions,
            usage_state,
            dashboard_notification_state,
        ),
        session_application=SessionApplicationService(
            event_store,
            queries,
            terminal_input,
            diagnostics,
            memory,
        ),
        controls=controls,
        hooks=HarnessHookService(
            registry,
            delivery,
            controls,
            host,
        ),
        launcher=HarnessLauncherService(registry, terminal),
        catalog=catalog,
        terminal_input=terminal_input,
        terminal=terminal,
        observation_runner=ObservationRunner(registry, checkpoints, delivery),
        diagnostics=diagnostics,
        insights=ApplicationInsightsService(
            event_store,
            queries,
            terminal_input,
            diagnostics,
            repositories,
            top_project_count=dashboard_config.INSIGHTS_PROJECT_LIMIT,
        ),
        resumable_sessions=ResumableSessionService(
            event_store,
            queries,
            terminal_input,
            repositories,
            result_limit=dashboard_config.RESUMABLE_SESSION_LIMIT,
        ),
        browser_telemetry=browser_telemetry,
        memory=DashboardMemoryService(memory),
        host=host,
    )


def build_default_application() -> CanonicalApplication:
    from core.audit import db_path as diagnostic_database_path

    return build_application(default_data_directory(), diagnostic_database_path())
