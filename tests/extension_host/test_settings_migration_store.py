# Copyright (c) 2026 Zhambyl Yermagambet
"""Verify immutable migration reservations and atomic resolved settings writes."""

from pathlib import Path

import pytest

from extensions.models.lifecycle_state import ManagerClaim
from tests import storage_reads
from tests.extension_host import lifecycle_fixture as lifecycle, migration_store_fixture as fixture

RESOLUTION = "resolution"


def test_resolution_preserves_reserved_request(tmp_path: Path) -> None:
    """Success adds a resolution while the accepted candidate bytes stay unchanged."""
    case = fixture.migration_store(tmp_path)
    admitted = case.admit()
    assert storage_reads.read_extension_runtime(
        case.store, case.proposal.candidate.runtime_revision,
    ) == case.proposal.candidate
    finished = case.store.finish_extension_operation(lifecycle.completion(admitted).model_copy(update={
        RESOLUTION: case.resolution,
    }))
    assert finished.accepted and finished.state.committed_runtime == case.resolution.runtime
    assert finished.state.settings[0].settings == case.resolution.settings_changes[0].settings
    operation = lifecycle.repository(tmp_path).read_extension_operation(admitted.proposal.operation_id)
    assert operation is not None
    assert operation.proposal == case.proposal and operation.resolution == case.resolution
    _require_reserved_bytes(case)


def test_resolution_required_for_success(tmp_path: Path) -> None:
    """An unresolved candidate cannot become an active runtime."""
    case = fixture.migration_store(tmp_path)
    admitted = case.admit()
    before = case.store.read_extension_lifecycle()
    with pytest.raises(ValueError, match="resolved settings"):
        case.store.finish_extension_operation(lifecycle.completion(admitted))
    assert case.store.read_extension_lifecycle() == before


@pytest.mark.parametrize("field", ["runtime_revision", "catalog_revision", "packages"])
def test_resolution_rejects_changed_runtime(tmp_path: Path, field: str) -> None:
    """A worker result cannot change the reserved runtime, catalog, or owner set."""
    case = fixture.migration_store(tmp_path)
    admitted = case.admit()
    before = case.store.read_extension_lifecycle()
    updates = {"runtime_revision": "another-runtime", "catalog_revision": 999, "packages": ()}
    resolution = case.resolution.model_copy(update={
        "runtime": case.resolution.runtime.model_copy(update={field: updates[field]}),
    })
    with pytest.raises(ValueError, match="runtime selection"):
        case.store.finish_extension_operation(lifecycle.completion(admitted).model_copy(update={
            RESOLUTION: resolution,
        }))
    assert case.store.read_extension_lifecycle() == before


def test_resolution_rejects_lost_override(tmp_path: Path) -> None:
    """A complete-looking reply cannot drop an explicit workspace choice."""
    case = fixture.migration_store(tmp_path)
    admitted = case.admit()
    change = case.resolution.settings_changes[0]
    resolution = case.resolution.model_copy(update={"settings_changes": (
        change.model_copy(update={"settings": change.settings.model_copy(update={"scopes": ()})}),
    )})
    with pytest.raises(ValueError, match="override scopes"):
        case.store.finish_extension_operation(lifecycle.completion(admitted).model_copy(update={
            RESOLUTION: resolution,
        }))
    assert case.store.read_extension_lifecycle().settings[0].settings == fixture.overrides(1)


def test_resolution_retry_is_exact(tmp_path: Path) -> None:
    """A completed request accepts its original resolution, not substitute output."""
    case = fixture.migration_store(tmp_path)
    admitted = case.admit()
    completion = lifecycle.completion(admitted).model_copy(update={RESOLUTION: case.resolution})
    assert case.store.finish_extension_operation(completion).accepted
    assert case.store.finish_extension_operation(completion).accepted
    changed = completion.model_copy(update={RESOLUTION: None})
    assert not case.store.finish_extension_operation(changed).accepted


def test_new_manager_rejects_old_resolution(tmp_path: Path) -> None:
    """Restart interrupts pending conversion and fences its late result."""
    case = fixture.migration_store(tmp_path)
    admitted = case.admit()
    claimed = case.store.claim_extension_manager(ManagerClaim(
        manager_id="next-manager", expected_revision=admitted.accepted_revision, claimed_at=lifecycle.NOW + 1,
    ))
    assert claimed.accepted
    completion = lifecycle.completion(admitted).model_copy(update={
        RESOLUTION: case.resolution,
    })
    assert not case.store.finish_extension_operation(completion).accepted
    assert case.store.read_extension_lifecycle() == claimed.state
    assert case.store.read_extension_lifecycle().settings[0].settings == fixture.overrides(1)


def _require_reserved_bytes(case: fixture.MigrationStore) -> None:
    with case.store.database.read() as connection:
        row = connection.execute(
            "SELECT selection FROM extension_runtime_revisions WHERE runtime_revision=?",
            (case.proposal.candidate.runtime_revision,),
        ).fetchone()
    assert row is not None
    assert row["selection"] == case.proposal.candidate.model_dump_json()
