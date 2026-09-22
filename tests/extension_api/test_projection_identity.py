# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep feed IDs stable across replay and separate across owners and causes."""

import pytest
from baqylau_extension_api.projection_identity import ProjectionEntryIdentity, projected_entry_id

from tests.extension_api import projection_samples, samples


def identity() -> ProjectionEntryIdentity:
    """Use the public V1 feed identity fields.

    Returns:
        A logical row with no runtime, history, or settings revision.

    """
    request = projection_samples.request()
    entry = projection_samples.entry()
    return ProjectionEntryIdentity(
        extension_id=request.binding.context.extension_id, scope=request.binding.context.scope,
        source_event_id=entry.source_event_id, entry_key=entry.entry_key,
    )


def test_projected_entry_id_has_fixed_encoding() -> None:
    """Detect a change to the fixed version-one identity algorithm."""
    digest = "f103e4ba7dc73118f09746570ebed4bfafd5509fb5365b1745b1976966e1709b"
    assert projected_entry_id(identity()) == f"projected:v1:test.sample:{digest}"


@pytest.mark.parametrize("change", [
    {"extension_id": "peer"}, {"scope": samples.SESSION}, {"source_event_id": "event-11"}, {"entry_key": "detail"},
])
def test_projected_entry_id_keeps_owner_and_cause(change: dict[str, object]) -> None:
    """Do not merge rows from different owners, scopes, causes, or local keys."""
    assert projected_entry_id(identity().model_copy(update=change)) != projected_entry_id(identity())


def test_projected_entry_identity_round_trips() -> None:
    """Preserve opaque Unicode keys through the exact wire encoding."""
    selected = identity().model_copy(update={"entry_key": "File name: 日本語/space here"})
    decoded = ProjectionEntryIdentity.model_validate_json(selected.model_dump_json())
    assert projected_entry_id(decoded) == projected_entry_id(selected)


def test_projection_identity_has_no_revisions() -> None:
    """Keep retries and candidate rebuilds outside logical feed identity."""
    assert set(ProjectionEntryIdentity.model_fields) == {"extension_id", "scope", "source_event_id", "entry_key"}
