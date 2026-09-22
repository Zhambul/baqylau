# Copyright (c) 2026 Zhambyl Yermagambet
"""Verify real keep, drop, replace, and insert semantics before storage integration."""

from baqylau_extension_api.core.sessions import SessionTitleChanged
from baqylau_extension_api.models.transforms import CanonicalTransformResult, Drop, Keep, Replace
from baqylau_extension_api.processing.canonical import apply_canonical_transform
from baqylau_extension_api.schemas import SchemaSet

from tests.extension_api import samples, transform_samples as fixtures


def test_empty_result_retains_inputs() -> None:
    """Treat omitted decisions as keep operations."""
    request = fixtures.canonical_request(fixtures.core_fact())
    assert apply_canonical_transform(request, CanonicalTransformResult(), SchemaSet(())) == request.inputs


def test_drop_all_returns_empty_batch() -> None:
    """Allow intentional suppression without an error or placeholder event."""
    request = fixtures.canonical_request(fixtures.core_fact())
    result = CanonicalTransformResult(operations=(Drop(input_id="core-1", reason="suppressed"),))
    assert not apply_canonical_transform(request, result, SchemaSet(()))


def test_replace_changes_payload_not_input() -> None:
    """Return a replacement while keeping the original object unchanged."""
    original = fixtures.core_fact()
    replacement = original.model_copy(update={"payload": SessionTitleChanged(title="Updated", origin="automatic")})
    result = CanonicalTransformResult(operations=(Replace(input_id=original.event_id, document=replacement),))
    assert apply_canonical_transform(fixtures.canonical_request(original), result, SchemaSet(())) == (replacement,)
    assert original.payload == SessionTitleChanged(title="Original", origin="custom")


def test_insertion_order_uses_input_positions() -> None:
    """Keep input order even when operations arrive in a different order."""
    first = fixtures.core_fact("first")
    second = fixtures.core_fact("second")
    before = fixtures.extension_addition(first, "before").model_copy(update={"position": "before"})
    after = fixtures.extension_addition(first, "after")
    result = CanonicalTransformResult(operations=(
        Keep(input_id=second.event_id), after, Drop(input_id=first.event_id, reason="specialized"), before,
    ))
    assert apply_canonical_transform(
        fixtures.canonical_request(first, second), result, SchemaSet((samples.schema_definition(),)),
    ) == (before.document, after.document, second)


def test_insertions_are_not_processed_again() -> None:
    """Return additions once; do not re-enter the same extension's transform."""
    original = fixtures.core_fact()
    addition = fixtures.extension_addition(original)
    response = CanonicalTransformResult(operations=(addition,))
    result = apply_canonical_transform(
        fixtures.canonical_request(original), response, SchemaSet((samples.schema_definition(),)),
    )
    assert result == (original, addition.document)
