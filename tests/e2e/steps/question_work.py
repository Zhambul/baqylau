# Copyright (c) 2026 Zhambyl Yermagambet
"""Steps that launch and assign question work."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pytest_bdd import parsers, when

from tests.e2e.testkit import references as refs
from tests.e2e.testkit.work_models import WorkRequest

if TYPE_CHECKING:
    from pathlib import Path

    from tests.e2e.testkit import question_contexts
    from tests.e2e.testkit.work_contexts import WorkLaunchContext


def _worker_kind(worker_name: str) -> refs.WorkerKind:
    try:
        return refs.WorkerKind(worker_name)
    except ValueError as error:
        message = f"unknown worker type {worker_name!r}"
        raise AssertionError(message) from error


def _bind_question_work(works: refs.Works, turns: refs.Turns, work_name: str, work: refs.WorkRef) -> None:
    works.bind(work_name, work)
    turns.bind(work_name, work.turn)


@when(parsers.parse('I launch session "{session_name}" with permission work "{work_name}" for an external file'))
def launch_permission_work(
    work_launch_context: WorkLaunchContext,
    tmp_path: Path,
    session_name: str,
    work_name: str,
) -> None:
    """Request a real file read outside the session workspace."""
    started = work_launch_context.driver.launch(
        work_launch_context.session_specs.get(session_name),
        _permission_request(work_name, tmp_path),
    )
    work_launch_context.sessions.bind(session_name, started.session)
    _bind_question_work(work_launch_context.works, work_launch_context.turns, work_name, started.work)


@when(parsers.parse('I assign permission work "{work_name}" in session "{session_name}" for an external file'))
def assign_permission_work(
    work_launch_context: WorkLaunchContext,
    tmp_path: Path,
    session_name: str,
    work_name: str,
) -> None:
    """Request file permission in an existing session."""
    work = work_launch_context.driver.assign(
        work_launch_context.session_specs.get(session_name),
        work_launch_context.sessions.get(session_name),
        _permission_request(work_name, tmp_path),
    )
    _bind_question_work(work_launch_context.works, work_launch_context.turns, work_name, work)


def _permission_request(work_name: str, directory: Path) -> WorkRequest:
    source = directory / "permission-source.txt"
    source.write_text("The access code is 731.", encoding="utf-8")
    prompt = (
        f"Read the exact file {source} with the native file-read tool. "
        "Do not use a shell or search other paths. "
        "After access is allowed, reply only with its content."
    )
    return WorkRequest(work_name, prompt, worker_kind=refs.WorkerKind.LEAD)


@when(
    parsers.parse(
        'I launch session "{session_name}" and assign question work "{work_name}" to the {worker_type} with prompt',
    ),
)
def launch_question_work(
    question_work_context: question_contexts.QuestionWorkContext,
    session_name: str,
    work_name: str,
    worker_type: str,
    docstring: str,
) -> None:
    """Launch one question-work session."""
    started = question_work_context.driver.launch(
        question_work_context.session_specs.get(session_name),
        WorkRequest(work_name, docstring.strip(), worker_kind=_worker_kind(worker_type)),
    )
    question_work_context.sessions.bind(session_name, started.session)
    _bind_question_work(question_work_context.works, question_work_context.turns, work_name, started.work)


@when(
    parsers.parse('I assign question work "{work_name}" in session "{session_name}" to the {worker_type} with prompt'),
)
def assign_question_work(
    question_work_context: question_contexts.QuestionWorkContext,
    session_name: str,
    work_name: str,
    worker_type: str,
    docstring: str,
) -> None:
    """Assign question work in one session."""
    work = question_work_context.driver.assign(
        question_work_context.session_specs.get(session_name),
        question_work_context.sessions.get(session_name),
        work_name=work_name,
        worker_kind=_worker_kind(worker_type),
        prompt=docstring.strip(),
    )
    _bind_question_work(question_work_context.works, question_work_context.turns, work_name, work)
