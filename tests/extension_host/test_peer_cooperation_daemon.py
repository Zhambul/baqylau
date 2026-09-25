# Copyright (c) 2026 Zhambyl Yermagambet
"""Two peers work alone and together in both orders, and an optional peer's removal ends only that read (C15, C16)."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Final, Literal

import pytest
from baqylau_extension_api.models.scopes import InstallationScope
from pydantic import TypeAdapter

from tests.extension_api import service_samples as peers
from tests.extension_host import (
    lifecycle_http_fixture as lifecycle,
    process_fixture,
    registry_process_fixture,
    source_daemon_fixture as source,
)

if TYPE_CHECKING:
    from pathlib import Path

    from sdk.client import BaqylauClient

SCOPE = InstallationScope().model_dump_json()
READ_PATH = f"/api/extensions/{peers.ALPHA}/queries/{peers.ALPHA}.read"
ENABLE: Final = "enable"
# The peer is installed but not active; the host reports why the service is unavailable.
ABSENT = "not_enabled"
JSON_HEADERS = (("Content-Type", "application/json"),)
TEST_TIMEOUT_SECONDS = 240


def install_peers(directory: Path, wheels: Path) -> None:
    """Write both peer packages into the daemon's package root."""
    for owner in (peers.ALPHA, peers.BETA):
        written = registry_process_fixture.write_peer(directory / owner, wheels, owner)
        written.rename(directory / "packages" / owner)


def peer_read(client: BaqylauClient) -> str:
    """Run alpha's read, which reads beta's declared service when beta is active.

    Returns:
        The read's text.

    """
    request = {"scope": SCOPE, "arguments": '"peer"'}
    reply = client.transport.client.post(READ_PATH, json=request)
    assert reply.status_code == HTTPStatus.OK, reply.text
    return TypeAdapter(str).validate_json(reply.json()["document"]["json_text"])


def change(client: BaqylauClient, owner: str, action: Literal["enable", "disable"]) -> None:
    """Change one package with its own request ID and wait for success."""
    request = lifecycle.lifecycle_request(client, owner, action, f"{action}-{owner}")
    admitted = client.extensions.lifecycle.change(owner, request)
    assert lifecycle.wait_operation(client, admitted.operation.operation_id).status == "succeeded"


def enable_both(client: BaqylauClient, *, beta_first: bool) -> None:
    """Enable both peers in the chosen order; alpha alone first reads that the peer is not active."""
    if beta_first:
        change(client, peers.BETA, ENABLE)
        change(client, peers.ALPHA, ENABLE)
        return
    change(client, peers.ALPHA, ENABLE)
    assert peer_read(client) == ABSENT
    change(client, peers.BETA, ENABLE)


@pytest.mark.timeout(TEST_TIMEOUT_SECONDS)
@pytest.mark.parametrize("beta_first", [False, True])
def test_peers_cooperate_in_both_orders(tmp_path: Path, runtime_wheels: Path, *, beta_first: bool) -> None:
    """Alpha alone reads no peer; with beta it reads beta; without beta again only that read changes."""
    case = source.installed(tmp_path, runtime_wheels)
    install_peers(tmp_path, runtime_wheels)
    with process_fixture.running_catalog(tmp_path) as client:
        case.change(client, ENABLE)
        enable_both(client, beta_first=beta_first)
        assert peer_read(client) == peers.PEER_VALUE
        change(client, peers.BETA, "disable")
        case.append('"after"\n')
        assert peer_read(client) == ABSENT
        source.require_facts(case, ('"first"\n', '"after"\n'))
        assert peers.ALPHA in lifecycle.active_owners(client)
