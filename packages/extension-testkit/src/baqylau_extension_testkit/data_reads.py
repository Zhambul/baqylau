# Copyright (c) 2026 Zhambyl Yermagambet
"""Read records, run queries and commands, and wait for jobs through the public HTTP routes."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import quote, urlencode

from baqylau_extension_testkit.data_models import (
    FINAL_JOB_STATES,
    CommandRequest,
    JobDocument,
    QueryReplyDocument,
    QueryRequest,
    RecordPageDocument,
)
from baqylau_extension_testkit.waiting import wait_until

if TYPE_CHECKING:
    from baqylau_extension_api.models.queries import QueryResult

    from baqylau_extension_testkit.client import HostClient

JOB_SECONDS = 60.0


def records(
    client: HostClient, owner: str, collection: str, scope: str, after: str = "",
) -> RecordPageDocument:
    """Read one page of an owner's records in one scope; the scope is its JSON document.

    Returns:
        The page and its continuation key.

    """
    selection = urlencode((("scope", scope), ("after", after)))
    path = _owner_path(owner, "records", collection)
    return client.read(f"{path}?{selection}", RecordPageDocument)


def query(client: HostClient, owner: str, query_id: str, request: QueryRequest) -> QueryResult:
    """Run one declared query.

    Returns:
        The ready or failed result.

    """
    path = _owner_path(owner, "queries", query_id)
    return client.send(path, request, QueryReplyDocument).root


def command(client: HostClient, owner: str, command_id: str, request: CommandRequest) -> JobDocument:
    """Submit one command and wait until its job has a final state.

    Returns:
        The final job; the caller checks its state and result.

    """
    path = _owner_path(owner, "commands", command_id)
    accepted = client.send(path, request, JobDocument)
    return wait_for_job(client, owner, accepted.job_id, request.scope)


def wait_for_job(client: HostClient, owner: str, job_id: str, scope: str) -> JobDocument:
    """Read one job until it has a final state.

    Returns:
        The final job.

    """
    selection = urlencode((("scope", scope),))
    path = f"{_owner_path(owner, 'jobs', job_id)}?{selection}"
    return wait_until(
        lambda: _final(client.read(path, JobDocument)), JOB_SECONDS, lambda: f"job {job_id} is not final",
    )


def _final(job: JobDocument) -> JobDocument | None:
    return job if job.state in FINAL_JOB_STATES else None


def _owner_path(owner: str, kind: str, name: str) -> str:
    owner_part = quote(owner, safe="")
    name_part = quote(name, safe="")
    return f"/api/extensions/{owner_part}/{kind}/{name_part}"
