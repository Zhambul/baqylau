# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject a real worker's failed or mismatched activation result before publication."""

from pathlib import Path

import pytest
from baqylau_extension_api.runtime.models import ExtensionTransportError

from extensions.runtime_preparation_contract import RuntimePreparationError
from tests.extension_api import service_samples as peers
from tests.extension_host import package_fixture, runtime_host_fixture as fixtures

READY = "return lifecycle_models.ActivationReady(runtime_revision=request.runtime_revision)"
FAILED = (
    "return lifecycle_models.ActivationFailed("
    "runtime_revision=request.runtime_revision, reason='test rejection')"
)
MISMATCHED = "return lifecycle_models.ActivationReady(runtime_revision='wrong-revision')"


@pytest.mark.parametrize(("reply", "exception_type", "message"), [
    (FAILED, RuntimePreparationError, "lifecycle did not accept"),
    (MISMATCHED, ExtensionTransportError, "RPC call failed"),
])
def test_worker_must_accept_exact_activation(
    tmp_path: Path, runtime_wheels: Path, reply: str, exception_type: type[RuntimeError], message: str,
) -> None:
    """A loaded process is not sufficient evidence that its candidate is ready."""
    source = fixtures.write_peers(tmp_path, runtime_wheels, (peers.ALPHA,))[0]
    _change_reply(source, reply)
    host = fixtures.host(tmp_path)
    operation = host.accept()
    with pytest.raises(exception_type, match=message):
        host.preparation.prepare_runtime(operation.proposal.candidate)
    assert not tuple((tmp_path / "environments").iterdir())
    assert host.store.read_extension_lifecycle().committed_runtime is None
    with host.preparation.registry.read_snapshot() as selected:
        assert not selected.snapshot.active_order


def _change_reply(source: Path, reply: str) -> None:
    code = (source / "src/peer_backend.py").read_text(encoding="utf-8")
    assert code.count(READY) == 1
    package_fixture.write_file(source, "src/peer_backend.py", code.replace(READY, reply).encode())
