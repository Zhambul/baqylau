# Copyright (c) 2026 Zhambyl Yermagambet
"""Permit peer display cooperation without an ownership transfer."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.projection_changes import ProjectionChange
from baqylau_extension_api.models.transforms import Drop, Replace

from tests.extension_api import projection_peer_samples as peers, projection_transform_checks as checks

CAPTURED_REVISION = 6


def test_transform_can_change_peer_feed_display() -> None:
    """Keep the peer's identity and schema while changing its display text."""
    original = peers.entry()
    replacement = original.model_copy(update={"entry": original.entry.model_copy(update={
        "summary": "Shared display",
    })})
    output = peers.apply(Replace[ProjectionChange](input_id=original.change_id, document=replacement))
    assert output == (replacement, peers.record())
    assert replacement.owner == peers.PEER


@pytest.mark.parametrize("change", [
    {"entry_key": "another"}, {"entry_type": "test.peer.another"}, {"source_event_id": "event-11"},
])
def test_transform_keeps_peer_feed_origin(change: dict[str, object]) -> None:
    """Do not relabel peer rows as different output or a different source."""
    original = peers.entry()
    replacement = original.model_copy(update={"entry": original.entry.model_copy(update=change)})
    with pytest.raises(ExtensionContractError, match="ownership, identity, or schema"):
        peers.apply(Replace[ProjectionChange](input_id=original.change_id, document=replacement))


def test_transform_cannot_take_peer_feed_owner() -> None:
    """Reject owner changes before schema validation or storage."""
    original = peers.entry()
    replacement = original.model_copy(update={"owner": "test.sample"})
    with pytest.raises(ExtensionContractError, match="ownership, identity, or schema"):
        peers.apply(Replace[ProjectionChange](input_id=original.change_id, document=replacement))


def test_transform_can_drop_peer_proposals() -> None:
    """Suppress peer proposals without deleting their captured records."""
    assert not peers.apply(
        Drop(input_id=peers.entry().change_id, reason="Hide row"),
        Drop(input_id=peers.record().change_id, reason="Keep stored record"),
    )
    assert peers.request().prior_records[0].revision == CAPTURED_REVISION


def test_transform_keeps_peer_record_owner() -> None:
    """A replacement cannot move a peer write into another owner's collection."""
    original = peers.record()
    key = original.write.key.model_copy(update={"owner": "test.sample", "collection": "test.sample.state"})
    replacement = original.model_copy(update={"write": original.write.model_copy(update={
        "key": key,
    })})
    with pytest.raises(ExtensionContractError, match="record identity"):
        peers.apply(Replace[ProjectionChange](input_id=original.change_id, document=replacement))


def test_transform_cannot_insert_peer_feed() -> None:
    """New output belongs to the current extension, not to a cooperating peer."""
    addition = checks.extension_addition(peers.entry())
    document = peers.entry().model_copy(update={
        "change_id": addition.document.change_id,
        "entry": peers.entry().entry.model_copy(update={"entry_key": addition.document.change_id}),
    })
    with pytest.raises(ExtensionContractError, match="belong to the selected extension"):
        peers.apply(addition.model_copy(update={"document": document}))
