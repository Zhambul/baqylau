# Copyright (c) 2026 Zhambyl Yermagambet
"""Accept and run durable commands against one active package double."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models import scopes

from api.extensions import command_service
from domain.extension_jobs import JobState
from extensions.control_policy import ExtensionControlPolicy, ExtensionReadOnlyError
from tests import command_job_fixture as jobs_fixture, command_package_fixture as packages

if TYPE_CHECKING:
    from tests import sqlite_repository_dependencies as repository_dependencies

OWNER = packages.OWNER
COMMAND_ID = packages.COMMAND_ID
SCOPE = packages.SCOPE
READ_ONLY = ExtensionControlPolicy(read_only=True)
REPOSITORY = scopes.RepositoryScope(
    repository_id="repository-one", worktree="/work/repository", git_directory="/work/repository/.git",
)


def test_command_runs_and_stores_its_result(
    main: repository_dependencies.SqliteDatabase,
) -> None:
    """A command executes once and stores its checked success."""
    fake_commands = packages.FakeCommands()
    command_dispatch = jobs_fixture.dispatch(main, fake_commands)
    job = command_service.run_command(command_dispatch, OWNER, COMMAND_ID, SCOPE, packages.a_request())

    assert job.state == JobState.SUCCEEDED
    assert job.result is not None
    assert len(fake_commands.requests) == 1


def test_command_replays_by_request_key(
    main: repository_dependencies.SqliteDatabase,
) -> None:
    """A repeated request key returns the stored job and does not run again."""
    fake_commands = packages.FakeCommands()
    command_dispatch = jobs_fixture.dispatch(main, fake_commands)
    first = command_service.run_command(command_dispatch, OWNER, COMMAND_ID, SCOPE, packages.a_request())
    second = command_service.run_command(command_dispatch, OWNER, COMMAND_ID, SCOPE, packages.a_request())

    assert first.job_id == second.job_id
    assert second.state == JobState.SUCCEEDED
    assert len(fake_commands.requests) == 1


def test_write_command_rejects_a_read_only_host(
    main: repository_dependencies.SqliteDatabase,
) -> None:
    """A write command does not run when the host is read-only."""
    fake_commands = packages.FakeCommands()
    command_dispatch = jobs_fixture.dispatch(main, fake_commands, effect="write", policy=READ_ONLY)
    request = packages.a_request().model_copy(update={"expected_state_revision": "state-1"})

    with pytest.raises(ExtensionReadOnlyError):
        command_service.run_command(command_dispatch, OWNER, COMMAND_ID, SCOPE, request)
    assert not fake_commands.requests


def test_read_command_ignores_the_write_policy(
    main: repository_dependencies.SqliteDatabase,
) -> None:
    """A read command runs even when the host is read-only."""
    fake_commands = packages.FakeCommands()
    command_dispatch = jobs_fixture.dispatch(main, fake_commands, policy=READ_ONLY)
    job = command_service.run_command(command_dispatch, OWNER, COMMAND_ID, SCOPE, packages.a_request())

    assert job.state == JobState.SUCCEEDED
    assert len(fake_commands.requests) == 1


def test_command_reports_a_missing_declaration(
    main: repository_dependencies.SqliteDatabase,
) -> None:
    """An undeclared command is not accepted."""
    fake_commands = packages.FakeCommands()
    command_dispatch = jobs_fixture.dispatch(main, fake_commands)

    with pytest.raises(ExtensionContractError):
        command_service.run_command(command_dispatch, OWNER, f"{OWNER}.missing", SCOPE, packages.a_request())
    assert not fake_commands.requests


def test_disabled_package_command_is_not_found(main: repository_dependencies.SqliteDatabase) -> None:
    """A disabled package exposes no command, so the host refuses the call (C24)."""
    enabled = packages.a_package(packages.FakeCommands())
    entry = enabled.entry.model_copy(update={"state": "disabled"})
    disabled = replace(enabled, entry=entry, environment=None, plugin=None)
    command_dispatch = command_service.CommandDispatch((disabled,), jobs_fixture.job_store(main))

    with pytest.raises(command_service.CommandNotFoundError):
        command_service.accept_command(command_dispatch, OWNER, COMMAND_ID, SCOPE, packages.a_request())


def test_repository_command_needs_no_session(main: repository_dependencies.SqliteDatabase) -> None:
    """A repository-scope command is accepted, run, and stored with no session row (C21)."""
    fake_commands = packages.FakeCommands()
    command_dispatch = jobs_fixture.repository_dispatch(main, fake_commands)

    job = command_service.run_command(command_dispatch, OWNER, COMMAND_ID, REPOSITORY, packages.a_request())

    assert job.state == JobState.SUCCEEDED
    assert job.scope == REPOSITORY
    assert fake_commands.requests[0].binding.scope == REPOSITORY
    with main.read() as connection:
        assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
