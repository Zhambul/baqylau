# Copyright (c) 2026 Zhambyl Yermagambet
"""Store a secret through write-only routes and resolve it in a real worker; no read returns it (P05-T05)."""

from pathlib import Path

import pytest
from baqylau_extension_api.models.credentials import SecretAvailable

from api.extensions.lifecycle_models import ExtensionOperationResponse
from api.extensions.secret_models import ExtensionSecretsResponse
from sdk.client import BaqylauClient
from sdk.transport import ApiFailureError
from tests.extension_host import (
    lifecycle_http_fixture as lifecycle,
    process_fixture,
    secret_worker_fixture as workers,
)

OWNER = workers.OWNER
STORED_TEXT = "correct-horse-battery-staple"


def enabled(client: BaqylauClient, request_id: str) -> ExtensionOperationResponse:
    """Enable the worker through a checked lifecycle request and wait for its result.

    Returns:
        The finished operation.

    """
    request = lifecycle.lifecycle_request(client, OWNER, "enable", request_id)
    admitted = client.extensions.lifecycle.change(OWNER, request)
    return lifecycle.wait_operation(client, admitted.operation.operation_id)


def configured(states: ExtensionSecretsResponse) -> list[tuple[str, bool]]:
    """Name each reference and whether it has a value.

    Returns:
        The name and state pairs.

    """
    return [(state.name, state.configured) for state in states.secrets]


def test_worker_resolves_a_stored_secret(tmp_path: Path, runtime_wheels: Path) -> None:
    """The worker reads the stored value through the host; the states and settings reads never show it."""
    workers.write_worker(tmp_path, runtime_wheels)
    with process_fixture.running_catalog(tmp_path) as client:
        stored = client.extensions.secrets.store(OWNER, workers.NAME, STORED_TEXT)
        operation = enabled(client, "enable-secret")
        settings = client.extensions.settings.read(OWNER)

    assert operation.status == "succeeded"
    assert workers.resolved(tmp_path) == SecretAvailable(name=workers.NAME, secret=STORED_TEXT)
    assert configured(stored) == [(workers.NAME, True)]
    for document in (stored, operation, settings):
        assert STORED_TEXT not in document.model_dump_json()


def test_cleared_secret_is_missing_in_the_worker(tmp_path: Path, runtime_wheels: Path) -> None:
    """A cleared value is missing for the worker and in the states."""
    workers.write_worker(tmp_path, runtime_wheels)
    with process_fixture.running_catalog(tmp_path) as client:
        client.extensions.secrets.store(OWNER, workers.NAME, STORED_TEXT)
        cleared = client.extensions.secrets.clear(OWNER, workers.NAME)
        enabled(client, "enable-cleared")

    assert workers.resolved(tmp_path).status == "missing"
    assert configured(cleared) == [(workers.NAME, False)]


def test_undeclared_and_read_only_are_refused(tmp_path: Path, runtime_wheels: Path) -> None:
    """An undeclared name is not found, and read-only mode refuses every secret write."""
    workers.write_worker(tmp_path, runtime_wheels)
    with process_fixture.running_catalog(tmp_path) as client, pytest.raises(ApiFailureError, match="404"):
        client.extensions.secrets.store(OWNER, "other", STORED_TEXT)
    with process_fixture.running_catalog(tmp_path, read_only=True) as client:
        with pytest.raises(ApiFailureError, match=r"403.*read-only"):
            client.extensions.secrets.store(OWNER, workers.NAME, STORED_TEXT)
        assert client.extensions.secrets.states(OWNER).read_only
