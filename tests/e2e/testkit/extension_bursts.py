# Copyright (c) 2026 Zhambyl Yermagambet
"""Send one extension query from many readers at the same moment, as the feed rows of one page do."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from baqylau_extension_testkit.client import HostClient
from baqylau_extension_testkit.data_models import QueryRequest
from pydantic import JsonValue, TypeAdapter

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from sdk import application_models
    from tests.e2e.testkit.extension_context import ExtensionContext

JSON: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
HEADERS = MappingProxyType({"Content-Type": "application/json"})


@dataclass(frozen=True)
class _Reader:
    """Post one prepared query when every reader is ready."""

    client: HostClient
    path: str
    body: str
    start: threading.Barrier

    def status(self, _reader: int) -> int:
        """Wait for the other readers, then post the query.

        Returns:
            The HTTP status of the read.

        """
        self.start.wait()
        transport = self.client.transport
        reply = transport.post(self.path, content=self.body, headers=dict(HEADERS))
        return reply.status_code


def parallel_statuses(
    client: HostClient, owner: str, query_id: str, scope: Mapping[str, str], count: int,
) -> list[int]:
    """Send one query with no filter from `count` readers at the same moment.

    Returns:
        The HTTP status of each read.

    """
    encoded_scope = JSON.dump_json(dict(scope)).decode()
    body = QueryRequest(scope=encoded_scope, arguments="{}").model_dump_json()
    reader = _Reader(client, f"/api/extensions/{owner}/queries/{query_id}", body, threading.Barrier(count))
    with ThreadPoolExecutor(count) as pool:
        return list(pool.map(reader.status, range(count)))


@dataclass(frozen=True)
class Burst:
    """The status of each read of a burst, and the audit rows that the burst left with one action."""

    statuses: tuple[int, ...]
    audit_rows: tuple[int, ...]


def burst(
    context: ExtensionContext,
    owner: str,
    query_id: str,
    scope: Mapping[str, str],
    count: int,
) -> Burst:
    """Send one burst of reads, and find the audit rows of its refusals.

    Returns:
        The statuses and the cursors of the rows named `extension query (<query_id>)`.

    """
    diagnostics = context.client.diagnostics
    before = diagnostics.checkpoint()
    statuses = parallel_statuses(context.host, owner, query_id, scope, count)
    report = diagnostics.report(before, diagnostics.checkpoint())
    return Burst(tuple(statuses), _rows(report.audit_problems, f"extension query ({query_id})"))


def _rows(problems: Iterable[application_models.models.AuditProblemResponse], action: str) -> tuple[int, ...]:
    return tuple(problem.error_cursor for problem in problems if problem.action == action)
