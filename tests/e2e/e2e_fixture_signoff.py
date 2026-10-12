# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide e2e fixture signoff."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.e2e import (
    e2e_fixture_catalogs,
    e2e_fixture_dependencies as fixture_dependencies,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

BACKGROUND_OUTPUT_FIXTURES = ("baqylau-e2e-background-redirect.log", "baqylau-e2e-background-pipe.log")


@fixture_dependencies.application.pytest.fixture
def attachment_launch_context(
    session_launch_context: fixture_dependencies.drivers.launching.SessionLaunchContext,
    staged_attachments: fixture_dependencies.drivers.refs.StagedAttachments,
) -> fixture_dependencies.drivers.launching.AttachmentLaunchContext:
    """Return services for one session launch with an attachment.

    Returns:
        Services for one session launch with an attachment.

    """
    return fixture_dependencies.drivers.launching.AttachmentLaunchContext(session_launch_context, staged_attachments)


@fixture_dependencies.application.pytest.fixture
def work_launch_context(
    work_driver: fixture_dependencies.testkit.WorkDriver,
    session_specs: fixture_dependencies.drivers.refs.SessionSpecs,
    sessions: fixture_dependencies.drivers.refs.Sessions,
    turns: fixture_dependencies.drivers.refs.Turns,
    works: fixture_dependencies.drivers.refs.Works,
) -> fixture_dependencies.contexts.work_contexts.WorkLaunchContext:
    """Return services for work launches.

    Returns:
        Services for work launches.

    """
    return fixture_dependencies.contexts.work_contexts.WorkLaunchContext(
        work_driver, session_specs, sessions, turns, works,
    )


@fixture_dependencies.application.pytest.fixture
def work_control_context(
    work_driver: fixture_dependencies.testkit.WorkDriver,
    session_specs: fixture_dependencies.drivers.refs.SessionSpecs,
    works: fixture_dependencies.drivers.refs.Works,
    worker_controls: fixture_dependencies.drivers.refs.WorkerControls,
) -> fixture_dependencies.contexts.work_contexts.WorkControlContext:
    """Return services for work controls.

    Returns:
        Services for work controls.

    """
    return fixture_dependencies.contexts.work_contexts.WorkControlContext(
        work_driver, session_specs, works, worker_controls,
    )


@fixture_dependencies.application.pytest.fixture
def plan_work_context(
    plan_work_driver: fixture_dependencies.contexts.PlanWorkDriver,
    session_specs: fixture_dependencies.drivers.refs.SessionSpecs,
    sessions: fixture_dependencies.drivers.refs.Sessions,
    turns: fixture_dependencies.drivers.refs.Turns,
) -> fixture_dependencies.contexts.planning_contexts.PlanWorkContext:
    """Return services for plan work.

    Returns:
        Services for plan work.

    """
    return fixture_dependencies.contexts.planning_contexts.PlanWorkContext(
        plan_work_driver, session_specs, sessions, turns,
    )


@fixture_dependencies.application.pytest.fixture
def question_work_driver(
    work_driver: fixture_dependencies.testkit.WorkDriver,
) -> fixture_dependencies.drivers.QuestionWorkDriver:
    """Build the question-work driver.

    Returns:
        The question driver backed by the supplied work driver.

    """
    return fixture_dependencies.drivers.QuestionWorkDriver(work_driver)


@fixture_dependencies.application.pytest.fixture
def skill_work_driver(
    work_driver: fixture_dependencies.testkit.WorkDriver,
    skill_fixtures: fixture_dependencies.contexts.skill_testkit.SkillFixtures,
) -> fixture_dependencies.contexts.skill_testkit.SkillWorkDriver:
    """Build the skill-work driver.

    Returns:
        The driver that uses the supplied work driver and skill fixtures.

    """
    return fixture_dependencies.contexts.skill_testkit.SkillWorkDriver(work_driver, skill_fixtures)


@fixture_dependencies.application.pytest.fixture(autouse=True)
def scenario_signoff(
    application_process: fixture_dependencies.drivers.process_testkit.ApplicationProcess,
    pipeline_check: fixture_dependencies.drivers.expected_findings.PipelineCheck,
    sessions: fixture_dependencies.drivers.refs.Sessions,
    wait_policy: fixture_dependencies.drivers.WaitPolicy,
    workspace: str,
) -> Iterator[None]:
    """Process scenario signoff."""
    client = pipeline_check.client
    for name in BACKGROUND_OUTPUT_FIXTURES:
        fixture_dependencies.application.Path(workspace, name).unlink(missing_ok=True)
    start = client.diagnostics.checkpoint()
    with fixture_dependencies.standard.contextlib.ExitStack() as cleanup:
        cleanup.callback(e2e_fixture_catalogs.restart_application, application_process, client)
        yield
        e2e_fixture_catalogs.finish_scenario_sessions(client, sessions, wait_policy)
        end = client.diagnostics.wait_until_drained(wait_policy.pipeline)
        pipeline_check.assert_clean("the scenario has pipeline findings", start, end)
