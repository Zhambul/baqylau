"""Build the canonical application graph with installed concrete harnesses."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from dashboard import config as dashboard_config

from harness.contract import CanonicalEventReaction, CoreTranslator
from harness.models import LIVENESS_SOURCE_TYPE, OUTPUT_LOCATION_SOURCE_TYPE
from core.data import data_directory
from engine.queries.content import CanonicalContentService
from engine.interpret.translators import LivenessTranslator, OperationOutputTranslator
from diagnostics import record as diagnostic_record
from diagnostics.read import OperationalDiagnostics
from harness.hooks.gateway import HookGatewayService
from app.services.insights import ApplicationInsightsService
from engine.interpret.loop import Interpreter
from terminal.panes.commands import PaneCommandService
from terminal.panes.streams import PaneStreamService
from harness.impl import installed
from engine.interpret.reactions import (
    OperationOutputCanonicalEventReaction,
    SessionUpsertCanonicalEventReaction,
)
from terminal.panes.reaction import PaneCanonicalEventReaction
from core.repository import RepositoryQueries
from app.services.resume import ResumableSessionService
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
from engine.store.canonical import CanonicalEventStore
from engine.queries.evidence import EvidenceQueries
from harness.registry import HarnessRegistry
from engine.store.output import OperationOutputStore
from engine.projections import SessionQueries
from engine.store.recorder import RawEventRecorder
from engine.store.sessions import SessionStore
from terminal.adapter import TerminalAdapter
from terminal.impl import resolve as resolve_terminal
from terminal.impl.null import null_plugin


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
    terminal: TerminalAdapter
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
    for plugin in installed():
        registry.register(plugin)
    registry.validate()
    sessions = SessionStore(database_path, registry)
    # The terminal is resolved ONCE, here, and passed down as fields: a
    # consumer holds the sub-protocol it needs, never the resolver. When no
    # terminal is installed the null plugin takes the seat, so every service
    # below stays unconditional and "no terminal" reads out of the audit as an
    # ordinary failure reason.
    terminal_plugin = resolve_terminal() or null_plugin()
    terminal = TerminalAdapter(terminal_plugin, sessions)
    queries = SessionQueries(canonical_store, sessions)
    controls = HarnessControlService(sessions, terminal, terminal_plugin, queries)
    catalog = HarnessCatalogService(registry)
    usage_state = ApplicationUsageState(HarnessUsageService(registry))
    terminal_input = TerminalInputService(sessions, terminal, terminal_plugin.viewport)
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
        launcher=HarnessLauncherService(registry, terminal, terminal_plugin.tabs),
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
        browser_telemetry=BrowserTelemetryService(diagnostic_record),
    )


def build_default_application() -> CanonicalApplication:
    from diagnostics.database import db_path as diagnostic_database_path

    return build_application(default_data_directory(), diagnostic_database_path())
