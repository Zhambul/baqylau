# Copyright (c) 2026 Zhambyl Yermagambet
"""Accept public peer commands as durable jobs with the same host checks as frontend commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.service_jobs import ServiceJobRequest

from domain.ids import ExtensionJobId
from extensions.control_policy import ExtensionReadOnlyError
from repository.contract.extension_jobs import CommandJobRequest
from tests.extension_api import service_samples as peers
from tests.extension_host import peer_beta_fixture as beta, peer_commands_fixture as peer
from tests.extension_host.peer_beta_fixture import SCOPE

if TYPE_CHECKING:
    from repository.impl.sqlite.connection import SqliteDatabase


def test_peer_command_is_accepted_once(main: SqliteDatabase) -> None:
    """A granted caller gets one durable job for one request key, and the host schedules it."""
    case = peer.a_case(main)

    first = case.submit(beta.a_command())
    second = case.submit(beta.a_command())

    assert first.state == "accepted"
    assert second.job_id == first.job_id
    assert [key.job_id for key in case.scheduler.submitted] == [first.job_id, first.job_id]
    stored = case.peer_jobs.jobs.read(peers.BETA, SCOPE, ExtensionJobId(first.job_id))
    assert stored is not None
    assert stored.request_key == f"peer:{peers.ALPHA}:peer-key"


def test_peer_command_needs_a_live_host_call(main: SqliteDatabase) -> None:
    """A worker cannot submit peer work outside a host call to it."""
    case = peer.a_case(main)

    with pytest.raises(ExtensionContractError):
        case.access().submit_service_command(beta.a_command())


def test_private_command_is_refused(main: SqliteDatabase) -> None:
    """A command that the service does not expose is refused before acceptance."""
    case = peer.a_case(main)

    with pytest.raises(ExtensionContractError, match="not exposed"):
        case.submit(beta.a_command(command_id=f"{peers.BETA}.private"))


def test_read_only_host_refuses_a_peer_write(main: SqliteDatabase) -> None:
    """A write peer command follows the same read-only policy as a frontend write (C24)."""
    case = peer.a_case(main, "write", read_only=True)

    with pytest.raises(ExtensionReadOnlyError):
        case.submit(beta.a_command())


def test_caller_reads_only_its_own_jobs(main: SqliteDatabase) -> None:
    """A frontend job of the same owner is not readable through a peer service."""
    case = peer.a_case(main)
    case.peer_jobs.jobs.accept_command(CommandJobRequest(
        owner=peers.BETA, scope=SCOPE, job_id=ExtensionJobId("frontend-job"), request_key="frontend-key",
        binding="{}", request="{}",
    ))
    assert case.caller.environment is not None

    grant = case.ledger.root(case.caller.environment, SCOPE, peer.GRANT_SECONDS)
    request = ServiceJobRequest(binding=beta.a_command().binding, job_id="frontend-job")

    with grant, pytest.raises(ExtensionContractError, match="not submitted by this caller"):
        case.access().read_service_job(request)
