# Copyright (c) 2026 Zhambyl Yermagambet
"""Read one stored durable job through the HTTP route."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from baqylau_extension_api.models import scopes

from domain.ids import ExtensionJobId
from repository.contract.extension_jobs import CommandJobRequest
from repository.impl.sqlite import databases
from tests import (
    http_test_assets,
    http_test_controls,
    http_test_server_runtime,
    sqlite_migration_events as events,
    sqlite_repository_dependencies as repository_dependencies,
)

if TYPE_CHECKING:
    from pathlib import Path

OWNER = "test.owner"
SCOPE = scopes.InstallationScope()
BINDING = '{"job_id":"job-1","request_key":"r1","call_id":"c1"}'
REQUEST = '{"arguments":{"x":1},"settings_revision":1}'
DATA_DIRECTORY = "data"
DATABASE_NAME = "main.db"
FIRST_REVISION = 1


def seed_job(tmp_path: Path) -> None:
    """Commit one accepted command into the private application database."""
    database = databases.main_database(str(tmp_path / DATA_DIRECTORY / DATABASE_NAME))
    events.populate(database)
    repository_dependencies.SqliteExtensionJobRepository(database).accept_command(CommandJobRequest(
        owner=OWNER, scope=SCOPE, job_id=ExtensionJobId("job-1"), request_key="r1", binding=BINDING, request=REQUEST,
    ))


def test_job_route_returns_a_stored_job(tmp_path: Path) -> None:
    """The job route returns the stored state and revision."""
    seed_job(tmp_path)
    query = urlencode({"scope": SCOPE.model_dump_json()})

    with http_test_assets.running_server(http_test_server_runtime.application()) as server:
        response = http_test_controls.get(server, f"/api/extensions/{OWNER}/jobs/job-1?{query}")

    document = response[2].json
    assert response[0] == HTTPStatus.OK
    identity = (document["job_id"], document["kind"])
    assert identity == ("job-1", "command")
    assert (document["state"], document["revision"]) == ("accepted", FIRST_REVISION)


def test_job_route_rejects_an_unknown_job(tmp_path: Path) -> None:
    """An absent job identity is a 404."""
    seed_job(tmp_path)
    query = urlencode({"scope": SCOPE.model_dump_json()})

    with http_test_assets.running_server(http_test_server_runtime.application()) as server:
        response = http_test_controls.get(server, f"/api/extensions/{OWNER}/jobs/missing?{query}")

    assert response[0] == HTTPStatus.NOT_FOUND
