# Copyright (c) 2026 Zhambyl Yermagambet
"""Check candidate facts and operation-specific wire validation."""

import pytest
from baqylau_extension_api.models.events import ExtensionFact
from baqylau_extension_api.models.raw_transforms import RawTransformResult
from baqylau_extension_api.models.transforms import Drop, Insert, Keep, RawTransformRequest, Replace
from pydantic import ValidationError

from tests.extension_api import samples as fixtures

EVENT_ID = "event-1"


def test_transform_operations_round_trip() -> None:
    """Retain each operation and its declared payload type."""
    response = RawTransformResult(operations=(
        Keep(input_id="raw-1"),
        Drop(input_id="raw-2", reason="duplicate"),
        Replace(document=fixtures.raw_input("raw-3"), input_id="raw-3"),
        Insert(document=fixtures.raw_input("derived-1"), input_id="raw-1", output_key="extra", position="after"),
    ))
    assert RawTransformResult.model_validate_json(response.model_dump_json()) == response


@pytest.mark.parametrize("operation", [
    '{"kind":"drop","input_id":"raw-1"}',
    '{"kind":"keep","input_id":"raw-1","document":{}}',
    '{"kind":"replace","input_id":"raw-1","document":{"unknown":true}}',
    '{"kind":"insert","input_id":"raw-1","output_key":"extra","position":"after"}',
    '{"kind":"unknown","input_id":"raw-1"}',
])
def test_transform_rejects_wrong_fields(operation: str) -> None:
    """Require the correct fields for each operation tag."""
    with pytest.raises(ValidationError):
        RawTransformResult.model_validate_json(f'{{"operations":[{operation}]}}')


def test_empty_transform_batch_is_valid() -> None:
    """Allow checkpoints with no inputs and transforms with no changes."""
    request = RawTransformRequest(context=fixtures.processing_context(), inputs=())
    assert RawTransformRequest.model_validate_json(request.model_dump_json()) == request
    assert RawTransformResult().operations == ()


@pytest.mark.parametrize("event_type", ["other.created", f"{fixtures.EXTENSION_ID}."])
def test_fact_requires_schema_owner(event_type: str) -> None:
    """Reject another owner's event type and an empty event type name."""
    with pytest.raises(ValidationError, match="namespace"):
        ExtensionFact(
            event_id=EVENT_ID, scope=fixtures.SESSION, event_type=event_type, document=fixtures.encoded_document(),
        )


@pytest.mark.parametrize("causes", [("cause-1", "cause-1"), (EVENT_ID,)])
def test_fact_rejects_repeated_or_self_causes(causes: tuple[str, ...]) -> None:
    """Keep the local cause list free of duplicate and self references."""
    with pytest.raises(ValidationError, match="cause references"):
        ExtensionFact(
            event_id=EVENT_ID, scope=fixtures.SESSION, event_type=f"{fixtures.EXTENSION_ID}.created",
            document=fixtures.encoded_document(), causes=causes,
        )


def test_fact_rejects_non_finite_time() -> None:
    """Do not accept a non-finite source timestamp."""
    with pytest.raises(ValidationError, match="finite"):
        ExtensionFact(
            event_id=EVENT_ID, scope=fixtures.SESSION, event_type=f"{fixtures.EXTENSION_ID}.created",
            document=fixtures.encoded_document(), occurred_at=float("nan"),
        )
