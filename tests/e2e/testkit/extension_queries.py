# Copyright (c) 2026 Zhambyl Yermagambet
"""Read extension queries in the live application, and decode their documents."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from baqylau_extension_api.models.queries import QueryReady
from baqylau_extension_testkit.client import HostRequestError
from baqylau_extension_testkit.data_models import QueryRequest
from baqylau_extension_testkit.data_reads import query
from pydantic import JsonValue, TypeAdapter

from tests.e2e.testkit.extension_sources import PACKAGES

if TYPE_CHECKING:
    from collections.abc import Mapping

    from baqylau_extension_api.models.queries import QueryResult
    from baqylau_extension_testkit.client import HostClient

JSON: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
DOCUMENT: TypeAdapter[dict[str, Any]] = TypeAdapter(dict[str, Any])
ADAPTERS = PACKAGES["adapters"].owner
GIT = PACKAGES["git"].owner
INSTALLATION = MappingProxyType({"kind": "installation"})


def answers(client: HostClient, owner: str, query_id: str, scope: Mapping[str, str]) -> bool:
    """Tell whether a package answers one query with no arguments.

    Returns:
        True for a ready answer; a disabled package has no query, and the host refuses it.

    """
    try:
        return isinstance(_result(client, owner, query_id, scope, {}), QueryReady)
    except HostRequestError:
        return False


def read(
    client: HostClient, owner: str, query_id: str, scope: Mapping[str, str], arguments: JsonValue,
) -> dict[str, Any]:
    """Run one query and decode its ready document.

    Returns:
        The decoded document.

    """
    result = _result(client, owner, query_id, scope, arguments)
    assert isinstance(result, QueryReady), f"{query_id} did not answer: {result}"
    return DOCUMENT.validate_json(result.document.json_text)


def invocation_outcomes(
    client: HostClient, scope: Mapping[str, str], group: str,
) -> set[str | None]:
    """Read the outcomes of the adapters calls of one group in one session.

    Returns:
        The outcomes.

    """
    rows = read(client, ADAPTERS, f"{ADAPTERS}.invocations", scope, {"group": group})["rows"]
    invocations = [row["invocation"] for row in rows]
    return {invocation.get("outcome") for invocation in invocations}


def repository_scope(client: HostClient, path: str) -> dict[str, str]:
    """Resolve the repository of one directory through the git package.

    Returns:
        The repository scope.

    """
    repository = read(client, GIT, f"{GIT}.resolve", INSTALLATION, {"path": path})["repository"]
    assert repository is not None, f"{path} is not in a repository"
    return {"kind": "repository", **repository}


def git_operations(client: HostClient, scope: Mapping[str, str]) -> set[str]:
    """Read the operations in a repository's adapters activity.

    Returns:
        The operations.

    """
    entries = read(client, GIT, f"{GIT}.activity", scope, {})["entries"]
    return {entry["operation"] for entry in entries}


def _result(
    client: HostClient, owner: str, query_id: str, scope: Mapping[str, str], arguments: JsonValue,
) -> QueryResult:
    encoded_scope = JSON.dump_json(dict(scope)).decode()
    request = QueryRequest(scope=encoded_scope, arguments=JSON.dump_json(arguments).decode())
    return query(client, owner, query_id, request)
