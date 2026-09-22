# Copyright (c) 2026 Zhambyl Yermagambet
"""Close drained resources without losing unresolved-work evidence or native ownership."""

from contextlib import closing
from pathlib import Path

import pytest
from baqylau_extension_api.models.lifecycle import DeactivationResult

from extensions.manager_contract import ManagerCleanupError
from extensions.runtime_ownership import FilesystemRuntimeOwnership
from extensions.runtime_ownership_contract import RuntimeBusyError
from tests.extension_api import samples, service_samples
from tests.extension_host import (
    lifecycle_fixture,
    registry_fixture,
    retirement_fixture,
    shutdown_fixture as fixtures,
    shutdown_storage_fixture as storage,
)


@pytest.mark.parametrize("failed_stop", [False, True])
def test_shutdown_retains_uncertainty(tmp_path: Path, *, failed_stop: bool) -> None:
    """A lost acknowledgement does not turn an external job into a completed job."""
    probe = retirement_fixture.RetirementProbe(
        registry_fixture.peer(service_samples.ALPHA),
        DeactivationResult(runtime_revision=samples.RUNTIME_REVISION, pending_job_ids=("uncertain-job",)),
        fail_stop=failed_stop,
    )
    session = fixtures.session(tmp_path, probe)
    with closing(session.services.lease), closing(fixtures.controller(session)) as host:
        host.close()
        record = host.read_state().lifecycle.last_shutdown
        assert record is not None and record.runtimes[0].resources_closed
        assert record.runtimes[0].issues == host.read_state().cleanup
        assert record.runtimes[0].issues == (storage.stop_issue(failed_stop=failed_stop),)
        with closing(FilesystemRuntimeOwnership(tmp_path).acquire_runtime()):
            assert host.read_state().phase == "closed" and probe.close_count == 1


def test_failed_close_keeps_native_lease(tmp_path: Path) -> None:
    """Repeated close cannot convert a failed physical close into a clean stop."""
    probe = retirement_fixture.RetirementProbe(registry_fixture.peer(service_samples.ALPHA), fail_close=True)
    session = fixtures.session(tmp_path, probe)
    # The controlled close has no real worker. Release only the test's lease on exit.
    with closing(session.services.lease):
        host = fixtures.controller(session)
        with pytest.raises(ManagerCleanupError, match="cleanup"):
            host.close()
        record = host.read_state().lifecycle.last_shutdown
        assert record is not None and not record.runtimes[0].resources_closed
        assert host.read_state().cleanup[0].reason == "close_failed"
        with pytest.raises(RuntimeBusyError):
            FilesystemRuntimeOwnership(tmp_path).acquire_runtime()
        probe.fail_close = False
        with pytest.raises(ManagerCleanupError, match="cleanup"):
            host.close()
        assert probe.close_count == 1 and probe.stop_count == 1


@pytest.mark.parametrize("stored_before_failure", [0, 1])
def test_storage_failure_prevents_lease_release(tmp_path: Path, stored_before_failure: int) -> None:
    """Do not repeat deactivation after physical resources have closed."""
    probe = retirement_fixture.RetirementProbe(registry_fixture.peer(service_samples.ALPHA), fail_stop=True)
    session = fixtures.session(tmp_path, probe)
    store = lifecycle_fixture.repository(tmp_path)
    with closing(session.services.lease), closing(fixtures.controller(session)) as host:
        with storage.reject_records(store, stored_before_failure):
            with pytest.raises(ManagerCleanupError, match="could not be stored"):
                host.close()
            assert probe.close_count == stored_before_failure
            with pytest.raises(RuntimeBusyError):
                FilesystemRuntimeOwnership(tmp_path).acquire_runtime()
        host.close()
        assert probe.close_count == 1
        expected_stops = 2 if stored_before_failure == 0 else 1
        assert probe.stop_count == expected_stops
        assert host.read_state().cleanup[0].reason == "deactivation_failed"
