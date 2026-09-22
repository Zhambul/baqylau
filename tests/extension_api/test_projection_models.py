# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject forged revisions, unsafe fallback text, and invalid row ownership."""

import pytest
from baqylau_extension_api.models.projection_entries import ProjectedEntry
from baqylau_extension_api.models.record_changes import PutRecord
from baqylau_extension_api.models.records import MissingRecord, RecordKey, StoredRecord
from pydantic import ValidationError

from tests.extension_api import projection_samples

REVISION = "revision"


@pytest.mark.parametrize("collection", ["peer.records", "test.sample.", "test.sample"])
def test_record_key_requires_owned_collection(collection: str) -> None:
    """A record key cannot claim a foreign or empty collection name."""
    with pytest.raises(ValidationError):
        RecordKey.model_validate(projection_samples.record_key().model_copy(update={"collection": collection}))


@pytest.mark.parametrize(REVISION, [-1, 0, "1", True])
def test_stored_record_revision_is_strict(revision: object) -> None:
    """Only positive host commit revisions identify stored rows."""
    with pytest.raises(ValidationError):
        StoredRecord.model_validate(projection_samples.stored_record().model_copy(update={REVISION: revision}))


@pytest.mark.parametrize(REVISION, [-1, "0", True])
def test_record_write_expected_revision_is_strict(revision: object) -> None:
    """Do not coerce a string or boolean into a record write precondition."""
    change = projection_samples.result().record_changes[0]
    with pytest.raises(ValidationError):
        PutRecord.model_validate(change.model_copy(update={"expected_revision": revision}))


@pytest.mark.parametrize("summary", ["\x1b[2J", "\x00", "\u202ehidden", "\x7f"])
def test_projection_summary_rejects_controls(summary: str) -> None:
    """Keep generic fallback text safe for the web and Kitty clients."""
    with pytest.raises(ValidationError):
        ProjectedEntry.model_validate(projection_samples.entry().model_copy(update={"summary": summary}))
    with pytest.raises(ValidationError):
        StoredRecord.model_validate(projection_samples.stored_record().model_copy(update={"summary": summary}))


def test_record_write_cannot_allocate_revision() -> None:
    """Host commit metadata is not an extension write field."""
    change = projection_samples.result().record_changes[0]
    with pytest.raises(ValidationError):
        PutRecord.model_validate(change.model_copy(update={REVISION: 1}))


@pytest.mark.parametrize(REVISION, [-1, 1, "0", False])
def test_missing_record_revision_is_strict(revision: object) -> None:
    """A deletion marker must not be replaced with a fake missing row."""
    with pytest.raises(ValidationError):
        MissingRecord.model_validate({"key": projection_samples.record_key(), REVISION: revision})
