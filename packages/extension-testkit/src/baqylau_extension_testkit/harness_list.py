# Copyright (c) 2026 Zhambyl Yermagambet
"""Ask a private host for the harnesses that it discovered."""

from __future__ import annotations

import tempfile
from contextlib import closing
from pathlib import Path

from pydantic import TypeAdapter

from baqylau_extension_testkit.client import HostClient
from baqylau_extension_testkit.host_process import HostExecutable, HostProcess
from baqylau_extension_testkit.isolation import PrivateRoots
from baqylau_extension_testkit.lifecycle_models import HostDocument


class HarnessDocument(HostDocument):
    """Name one harness that the host discovered."""

    name: str


def discovered_harnesses(executable: HostExecutable) -> frozenset[str]:
    """Start a private host, read its harness list, and stop it.

    Returns:
        The harness names.

    """
    with (
        tempfile.TemporaryDirectory(prefix="baqylau-harnesses-") as directory,
        HostProcess.start(executable, PrivateRoots(Path(directory))) as process,
        closing(HostClient(process.url)) as client,
    ):
        client.wait_until_ready(process)
        return _harness_names(client)


def _harness_names(client: HostClient) -> frozenset[str]:
    reply = client.transport.get("/api/harnesses")
    reply.raise_for_status()
    harnesses = TypeAdapter(tuple[HarnessDocument, ...]).validate_json(reply.content)
    return frozenset(harness.name for harness in harnesses)
