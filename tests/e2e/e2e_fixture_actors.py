# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide e2e fixture actors."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from tests.e2e import (
    e2e_fixture_dependencies as fixture_dependencies,
    e2e_fixture_planning,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

STALL_CHECK_SECONDS = 30
STALL_REPORT_CHECK_COUNT = 2
SESSION_SCOPE: Literal["session"] = "session"


@fixture_dependencies.application.pytest.fixture(scope=SESSION_SCOPE)
def client(
    application_process: fixture_dependencies.drivers.process_testkit.ApplicationProcess,
    expected_audit_errors: fixture_dependencies.drivers.expected_findings.ExpectedAuditErrors,
) -> Iterator[fixture_dependencies.harness.BaqylauClient]:
    """Open a client and check diagnostics when the test session ends.

    Yields:
        The connected client after the application is ready.

    """
    running = fixture_dependencies.harness.BaqylauClient(application_process.endpoint.url)
    running.application.wait_until_ready()
    start = running.diagnostics.checkpoint()
    with fixture_dependencies.standard.contextlib.closing(running):
        yield running
        end = running.diagnostics.wait_until_drained()
        fixture_dependencies.drivers.process_testkit.assert_clean_diagnostics(
            "the complete E2E run has pipeline findings", running.diagnostics.report(start, end),
            expected_audit_errors.cursors,
        )


def report_stalls(
    stop_event: fixture_dependencies.application.threading.Event,
    application_process: fixture_dependencies.drivers.process_testkit.ApplicationProcess,
    test_node: fixture_dependencies.application.pytest.Item,
) -> None:
    """Write a diagnostic report after two checks without progress."""
    started_at = fixture_dependencies.application.time.monotonic()
    previous = fixture_dependencies.harness.failure_diagnostics.e2e_progress_marker(application_process)
    unchanged = 0
    while not stop_event.wait(STALL_CHECK_SECONDS):
        current = fixture_dependencies.harness.failure_diagnostics.e2e_progress_marker(application_process)
        if current == previous:
            unchanged += 1
        else:
            previous = current
            unchanged = 0
        if unchanged < STALL_REPORT_CHECK_COUNT:
            continue
        report = e2e_fixture_planning.stall_report(application_process, test_node, current, started_at)
        fixture_dependencies.standard.sys.stderr.write(report)
        fixture_dependencies.standard.sys.stderr.flush()
        unchanged = 0


@fixture_dependencies.application.pytest.fixture
def account_selection_context(
    client: fixture_dependencies.harness.BaqylauClient,
    session_specs: fixture_dependencies.drivers.refs.SessionSpecs,
    account_selections: fixture_dependencies.drivers.refs.AccountSelections,
    wait_policy: fixture_dependencies.drivers.WaitPolicy,
) -> fixture_dependencies.drivers.account_contexts.AccountSelectionContext:
    """Return services for session account selection.

    Returns:
        Services for session account selection.

    """
    return fixture_dependencies.drivers.account_contexts.AccountSelectionContext(
        client, session_specs, account_selections, wait_policy,
    )


@fixture_dependencies.application.pytest.fixture
def session_account_context(
    client: fixture_dependencies.harness.BaqylauClient,
    sessions: fixture_dependencies.drivers.refs.Sessions,
    account_selections: fixture_dependencies.drivers.refs.AccountSelections,
    wait_policy: fixture_dependencies.drivers.WaitPolicy,
) -> fixture_dependencies.drivers.account_contexts.SessionAccountContext:
    """Return services for session account checks.

    Returns:
        Services for session account checks.

    """
    return fixture_dependencies.drivers.account_contexts.SessionAccountContext(
        client, sessions, account_selections, wait_policy,
    )


@fixture_dependencies.application.pytest.fixture
def session_prompt_context(
    client: fixture_dependencies.harness.BaqylauClient,
    sessions: fixture_dependencies.drivers.refs.Sessions,
    turns: fixture_dependencies.drivers.refs.Turns,
) -> fixture_dependencies.testkit.session_contexts.SessionPromptContext:
    """Return services for prompt delivery.

    Returns:
        Services for prompt delivery.

    """
    return fixture_dependencies.testkit.session_contexts.SessionPromptContext(client, sessions, turns)


@fixture_dependencies.application.pytest.fixture
def session_launch_context(
    client: fixture_dependencies.harness.BaqylauClient,
    workspace: str,
    session_launch_references: fixture_dependencies.drivers.launching.SessionLaunchReferences,
    wait_policy: fixture_dependencies.drivers.WaitPolicy,
) -> fixture_dependencies.drivers.launching.SessionLaunchContext:
    """Return services for one named session launch.

    Returns:
        Services for one named session launch.

    """
    return fixture_dependencies.drivers.launching.SessionLaunchContext(
        client, workspace, session_launch_references, wait_policy,
    )


@fixture_dependencies.application.pytest.fixture
def work_driver(
    client: fixture_dependencies.harness.BaqylauClient,
    workspace: str,
    wait_policy: fixture_dependencies.drivers.WaitPolicy,
) -> fixture_dependencies.testkit.WorkDriver:
    """Build the work driver for the test workspace.

    Returns:
        The driver with the supplied client and wait policy.

    """
    return fixture_dependencies.testkit.WorkDriver(client, workspace, wait_policy)
