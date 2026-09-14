# Copyright (c) 2026 Zhambyl Yermagambet
"""Steps that launch, assign, and interrupt named work."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pytest_bdd import parsers, when

from tests.e2e.testkit.work_models import WorkRequest
from tests.e2e.testkit.work_workers import worker

if TYPE_CHECKING:
    from tests.e2e.testkit.references import Turns, WorkRef, Works
    from tests.e2e.testkit.work_contexts import WorkControlContext, WorkLaunchContext


def _bind_work(works: Works, turns: Turns, name: str, work: WorkRef) -> None:
    works.bind(name, work)
    turns.bind(name, work.turn)


@when(
    parsers.parse(
        'I launch session "{session_name}" and assign work "{work_name}" to the {worker_type} with prompt',
    ),
)
def launch_work(
    work_launch_context: WorkLaunchContext,
    session_name: str,
    work_name: str,
    worker_type: str,
    docstring: str,
) -> None:
    """Launch one work item."""
    kind, background = worker(worker_type)
    started = work_launch_context.driver.launch(
        work_launch_context.session_specs.get(session_name),
        WorkRequest(work_name, docstring.strip(), worker_kind=kind, background=background),
    )
    work_launch_context.sessions.bind(session_name, started.session)
    _bind_work(work_launch_context.works, work_launch_context.turns, work_name, started.work)


@when(
    parsers.parse(
        'I assign work "{work_name}" in session "{session_name}" to the {worker_type} with prompt',
    ),
)
def assign_work(
    work_launch_context: WorkLaunchContext,
    session_name: str,
    work_name: str,
    worker_type: str,
    docstring: str,
) -> None:
    """Assign one work item."""
    kind, background = worker(worker_type)
    work = work_launch_context.driver.assign(
        work_launch_context.session_specs.get(session_name),
        work_launch_context.sessions.get(session_name),
        WorkRequest(
            work_name,
            docstring.strip(),
            worker_kind=kind,
            background=background,
            named=worker_type == "named subagent",
        ),
    )
    _bind_work(work_launch_context.works, work_launch_context.turns, work_name, work)


@when(
    parsers.parse(
        'I request interruption of work "{work_name}" in session "{session_name}" as worker control "{control_name}"',
    ),
)
def interrupt_work(
    work_control_context: WorkControlContext,
    work_name: str,
    session_name: str,
    control_name: str,
) -> None:
    """Request interruption of one work item."""
    work = work_control_context.works.get(work_name)
    work_control_context.controls.bind(
        control_name,
        work_control_context.driver.interrupt(
            work_control_context.session_specs.get(session_name),
            work,
        ),
    )


@when(
    parsers.parse(
        'I launch session "{session_name}" and assign work "{work_name}" to a '
        "subagent with follow-up '{followup}' using prompt",
    ),
)
def launch_work_with_followup(
    work_launch_context: WorkLaunchContext,
    session_name: str,
    work_name: str,
    followup: str,
    docstring: str,
) -> None:
    """Launch work that has one follow-up."""
    started = work_launch_context.driver.launch_with_followup(
        work_launch_context.session_specs.get(session_name),
        work_name=work_name,
        prompt=docstring.strip(),
        followup=followup,
    )
    work_launch_context.sessions.bind(session_name, started.session)
    _bind_work(work_launch_context.works, work_launch_context.turns, work_name, started.work)


@when(
    parsers.parse(
        'I launch session "{session_name}" and assign work "{work_name}" to a '
        "subagent that sends '{message}' to the lead and returns '{result}'",
    ),
)
def launch_work_with_parent_message(
    work_launch_context: WorkLaunchContext,
    session_name: str,
    work_name: str,
    message: str,
    result: str,
) -> None:
    """Launch work that sends a message to its lead."""
    started = work_launch_context.driver.launch_with_parent_message(
        work_launch_context.session_specs.get(session_name),
        work_name=work_name,
        message=message,
        result=result,
    )
    work_launch_context.sessions.bind(session_name, started.session)
    _bind_work(work_launch_context.works, work_launch_context.turns, work_name, started.work)
