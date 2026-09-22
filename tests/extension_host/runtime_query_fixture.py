# Copyright (c) 2026 Zhambyl Yermagambet
"""Read prepared real workers only through a held registry and host-issued grants."""

from pydantic import TypeAdapter

from extensions.models import lifecycle_operations as lifecycle_models
from extensions.models.lifecycle_operations import LifecycleOperation
from extensions.runtime_commit import StoredRegistryCommit
from extensions.runtime_preparation_contract import PreparedExtensionRuntime
from tests.extension_api import service_samples as peers
from tests.extension_host import lifecycle_fixture, runtime_host_fixture


def read_peer(host: runtime_host_fixture.RuntimeHost, mode: str = "peer") -> str:
    """Call alpha against the active registry, with its exact runtime binding.

    Returns:
        A decoded package-owned text reply through the real private process.

    """
    with host.preparation.registry.read_snapshot() as selected:
        caller = next(
            package for package in selected.snapshot.packages if package.manifest.extension_id == peers.ALPHA
        )
        assert caller.plugin is not None and caller.plugin.capabilities.queries is not None
        assert caller.environment is not None
        original = peers.query_request(peers.ALPHA, mode)
        request = original.model_copy(update={
            "binding": original.binding.model_copy(update={
                "runtime_revision": selected.snapshot.directory.runtime_revision,
            }),
        })
        with host.preparation.ledger.root(caller.environment, request.binding.scope, 5):
            response = caller.plugin.capabilities.queries.query(request)
    assert response.status == "ready"
    return TypeAdapter(str).validate_json(response.document.json_text)


def publish(
    host: runtime_host_fixture.RuntimeHost, prepared: PreparedExtensionRuntime, operation: LifecycleOperation,
    expected_revision: int = 0,
) -> None:
    """Run the real joined commit without claiming that a daemon manager exists."""
    outcome = host.preparation.registry.publish_snapshot(expected_revision, prepared.snapshot, StoredRegistryCommit(
        host.store, operation, lifecycle_fixture.NOW + 1,
    ))
    assert outcome.status == "accepted"


def remove(host: runtime_host_fixture.RuntimeHost, expected_revision: int = 1) -> None:
    """Publish an empty committed set before the test closes borrowed workers."""
    proposed = lifecycle_fixture.proposal(host.store, operation_id="remove")
    operation = host.store.accept_extension_operation(proposed, lifecycle_fixture.NOW).operation
    assert operation is not None
    prepared = host.preparation.prepare_runtime(proposed.candidate)
    publish(host, prepared, operation, expected_revision)
    prepared.close()


def finish_failed(host: runtime_host_fixture.RuntimeHost, operation: LifecycleOperation) -> None:
    """Record test preparation failure without claiming automatic manager handling."""
    completion = lifecycle_fixture.completion(operation).model_copy(update={
        "failure": lifecycle_models.LifecycleFailure(code="preparation_failed", detail="Test startup failed."),
    })
    assert host.store.finish_extension_operation(completion).accepted
