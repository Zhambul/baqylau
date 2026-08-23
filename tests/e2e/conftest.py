"""Fixtures for the live harness feature suite."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from api.runtime import ApplicationConfig
from api.diagnostics.models import DiagnosticsReportResponse
from sdk.client import BaqylauClient
from tests.e2e.testkit.policy import WaitPolicy
from tests.e2e.testkit.process import ApplicationProcess
from tests.e2e.testkit.references import (
    Actors,
    Assignments,
    Compactions,
    Controls,
    FileOperations,
    HarnessCatalogs,
    HarnessLists,
    InsightsSnapshots,
    Plans,
    Questions,
    References,
    ResumableLists,
    SessionSpecs,
    Sessions,
    Shells,
    StagedAttachments,
    Skills,
    Tasks,
    Turns,
)

pytest_plugins = (
    "tests.e2e.steps.catalog",
    "tests.e2e.steps.attachments",
    "tests.e2e.steps.compactions",
    "tests.e2e.steps.controls",
    "tests.e2e.steps.files",
    "tests.e2e.steps.insights",
    "tests.e2e.steps.planning",
    "tests.e2e.steps.preferences",
    "tests.e2e.steps.questions",
    "tests.e2e.steps.scoreboard",
    "tests.e2e.steps.sessions",
    "tests.e2e.steps.shells",
    "tests.e2e.steps.skills",
    "tests.e2e.steps.subagents",
    "tests.e2e.steps.usage",
)

DEFAULT_WORKSPACE = os.path.expanduser("~/code/personal/baqylau-tests")
FILE_OPERATION_FIXTURE = "baqylau-e2e-file.txt"
PARENT_SESSION_VARIABLES = (
    "CLAUDECODE",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_EXECPATH",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_CODE_SSE_PORT",
    "CLAUDE_PID",
    "CLAUDE_EFFORT",
    "CLAUDE_OTEL_PORT",
    "CODEX_COMPANION_SESSION_ID",
    "BAQYLAU_LAUNCH_MODEL",
    "BAQYLAU_LAUNCH_EFFORT",
    "KITTY_WINDOW_ID",
)


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("baqylau live harness tests")
    group.addoption("--e2e-workspace", default=DEFAULT_WORKSPACE)
    group.addoption("--e2e-data-dir", default=None)
    group.addoption("--e2e-model", default=None)
    group.addoption("--e2e-effort", default=None)


@pytest.fixture(scope="session")
def workspace(pytestconfig: pytest.Config) -> str:
    directory = Path(str(pytestconfig.getoption("--e2e-workspace"))).expanduser().resolve()
    if not directory.is_dir():
        raise pytest.UsageError(f"workspace does not exist: {directory}")
    return str(directory)


@pytest.fixture(scope="session")
def application_process(
    pytestconfig: pytest.Config,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[ApplicationProcess]:
    configured = pytestconfig.getoption("--e2e-data-dir")
    data_directory = (
        Path(str(configured)).expanduser().resolve()
        if configured
        else tmp_path_factory.mktemp("baqylau-live-data")
    )
    process = ApplicationProcess.start(ApplicationConfig(
        data_directory=Path(data_directory),
        port=0,
        terminal="pty",
        notify_telegram=False,
        notify_webpush=False,
        environment_removals=PARENT_SESSION_VARIABLES,
        base_environment=dict(os.environ),
    ))
    try:
        yield process
    finally:
        exit_code = process.stop()
        assert exit_code == 0, f"application process exited with {exit_code}"


def _assert_report_is_clean(label: str, report: DiagnosticsReportResponse) -> None:
    findings = []
    if report.raw_event_count != report.verdict_count:
        findings.append(
            f"{report.raw_event_count - report.verdict_count} raw events have no verdict"
        )
    findings.extend(
        f"raw event {item.raw_event_cursor} {item.source_type}:{item.source_position} "
        f"has decision {item.decision!r}: {item.reason or 'no reason'}; {item.payload}"
        for item in report.interpretation_problems
    )
    findings.extend(
        f"audit error {item.error_cursor} {item.component} {item.action}: {item.context}"
        for item in report.audit_problems
    )
    assert not findings, label + ":\n" + "\n".join(findings)


@pytest.fixture(scope="session")
def client(application_process: ApplicationProcess) -> Iterator[BaqylauClient]:
    running = BaqylauClient(application_process.endpoint.url)
    running.application.wait_until_ready()
    start = running.diagnostics.checkpoint()
    try:
        yield running
        end = running.diagnostics.wait_until_drained()
        _assert_report_is_clean(
            "the complete E2E run has pipeline findings",
            running.diagnostics.report(start, end),
        )
    finally:
        running.close()


@pytest.fixture
def wait_policy() -> WaitPolicy:
    return WaitPolicy()


@pytest.fixture
def session_specs() -> SessionSpecs:
    return References("session configuration")


@pytest.fixture
def sessions() -> Sessions:
    return References("session")


@pytest.fixture
def turns() -> Turns:
    return References("turn")


@pytest.fixture
def shells() -> Shells:
    return References("shell command")


@pytest.fixture
def actors() -> Actors:
    return References("actor")


@pytest.fixture
def assignments() -> Assignments:
    return References("assignment")


@pytest.fixture
def file_operations() -> FileOperations:
    return References("file operation")


@pytest.fixture
def staged_attachments() -> StagedAttachments:
    return References("staged attachment")


@pytest.fixture
def controls() -> Controls:
    return References("control")


@pytest.fixture
def skills() -> Skills:
    return References("skill")


@pytest.fixture
def questions() -> Questions:
    return References("question")


@pytest.fixture
def plans() -> Plans:
    return References("plan")


@pytest.fixture
def tasks() -> Tasks:
    return References("task")


@pytest.fixture
def compactions() -> Compactions:
    return References("compaction")


@pytest.fixture
def harness_lists() -> HarnessLists:
    return References("harness list")


@pytest.fixture
def harness_catalogs() -> HarnessCatalogs:
    return References("harness catalog")


@pytest.fixture
def insights_snapshots() -> InsightsSnapshots:
    return References("insights snapshot")


@pytest.fixture
def resumable_lists() -> ResumableLists:
    return References("resumable list")


@pytest.fixture
def file_operation_path(workspace: str) -> Iterator[str]:
    path = os.path.join(workspace, FILE_OPERATION_FIXTURE)
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    try:
        yield path
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


@pytest.fixture(autouse=True)
def scenario_signoff(
    client: BaqylauClient,
    sessions: Sessions,
    wait_policy: WaitPolicy,
) -> Iterator[None]:
    start = client.diagnostics.checkpoint()
    yield
    for session in sessions.values():
        snapshot = client.sessions.snapshot(session)
        if snapshot.data.session.state != "finished":
            receipt = client.sessions.close(session)
            assert receipt.status_code in (200, 202), (
                f"cleanup action {receipt.request_id!r} was not accepted: {receipt.outcome}"
            )
        client.sessions.wait_until_finished(session, wait_policy.cleanup)
    end = client.diagnostics.wait_until_drained(wait_policy.pipeline)
    _assert_report_is_clean(
        "the scenario has pipeline findings",
        client.diagnostics.report(start, end),
    )
