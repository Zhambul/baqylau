# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject a complete canonical proposal when any operation is invalid."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.canonical import CanonicalFact
from baqylau_extension_api.models.transforms import CanonicalTransformResult, Drop, Replace
from baqylau_extension_api.processing.canonical import apply_canonical_transform
from baqylau_extension_api.schemas import SchemaSet

from tests.extension_api import samples, transform_samples as fixtures


def test_unknown_anchor_rejects_whole_batch() -> None:
    """Do not return a partial drop result before detecting a later invalid anchor."""
    request = fixtures.canonical_request(fixtures.core_fact())
    response = CanonicalTransformResult(operations=(
        Drop(input_id="core-1", reason="drop"), Drop(input_id="missing", reason="drop"),
    ))
    with pytest.raises(ExtensionContractError, match="unknown input"):
        apply_canonical_transform(request, response, SchemaSet(()))
    assert request.inputs == (fixtures.core_fact(),)


@pytest.mark.parametrize("replacement", [
    fixtures.core_fact().model_copy(update={"event_id": "claimed"}),
    fixtures.core_fact().model_copy(update={"raw_event_ids": ("unrelated",)}),
    fixtures.core_fact().model_copy(update={"turn_id": "unrelated"}),
])
def test_replacement_cannot_change_identity(replacement: CanonicalFact) -> None:
    """Reject changes to event identity and recorded source context."""
    response = CanonicalTransformResult(operations=(Replace(input_id="core-1", document=replacement),))
    with pytest.raises(ExtensionContractError):
        apply_canonical_transform(fixtures.canonical_request(fixtures.core_fact()), response, SchemaSet(()))


def test_insertion_requires_derived_identity() -> None:
    """Reject an arbitrary worker-assigned identity for an added event."""
    original = fixtures.core_fact()
    addition = fixtures.extension_addition(original)
    wrong = addition.document.model_copy(update={"event_id": "worker-chosen"})
    response = CanonicalTransformResult(operations=(addition.model_copy(update={"document": wrong}),))
    with pytest.raises(ExtensionContractError, match="derived identity"):
        apply_canonical_transform(
            fixtures.canonical_request(original), response, SchemaSet((samples.schema_definition(),)),
        )


def test_new_document_requires_registered_schema() -> None:
    """Reject a new extension fact if its schema is absent."""
    original = fixtures.core_fact()
    response = CanonicalTransformResult(operations=(fixtures.extension_addition(original),))
    with pytest.raises(ExtensionContractError, match="not registered"):
        apply_canonical_transform(fixtures.canonical_request(original), response, SchemaSet(()))
