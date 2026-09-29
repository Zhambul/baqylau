# Copyright (c) 2026 Zhambyl Yermagambet
"""Submit no job while a runtime switch is pending.

No job can run then, and each attempt borrows the registry, which the switch
needs free. A reload of the adapters package waited for minutes, because the
engine submitted the accepted observer jobs again on every pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from api.extensions import command_service
from domain.extension_jobs import JobState
from tests import (
    command_job_fixture as jobs_fixture,
    command_package_fixture as packages,
    job_executor_fixture,
    slow_command_fixture as slow,
)

if TYPE_CHECKING:
    from tests import sqlite_repository_dependencies as repository_dependencies


@dataclass(frozen=True)
class _Phase:
    switch_pending: bool


@dataclass(frozen=True)
class _Manager:
    phase: _Phase

    def read_state(self) -> _Phase:
        return self.phase


def test_no_job_is_submitted_during_a_switch(main: repository_dependencies.SqliteDatabase) -> None:
    """The accepted job waits for the switch; without a switch, the executor takes it."""
    case = slow.a_slow_case()
    dispatch = command_service.CommandDispatch((case.package,), jobs_fixture.job_store(main))
    command_service.accept_command(dispatch, packages.OWNER, packages.COMMAND_ID, packages.SCOPE, packages.a_request())
    stores = jobs_fixture.stores(main)
    pending = job_executor_fixture.an_executor(case.registry, stores, _Manager(_Phase(switch_pending=True)))

    assert pending.submit_accepted(10) == 0
    assert len(dispatch.jobs.jobs_in_state(JobState.ACCEPTED, 10)) == 1
    pending.close()
    running = job_executor_fixture.an_executor(case.registry, stores, _Manager(_Phase(switch_pending=False)))
    assert running.submit_accepted(10) == 1
    case.slow_commands.release.set()
    running.close()
