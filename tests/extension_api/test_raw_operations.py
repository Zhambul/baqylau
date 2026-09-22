# Copyright (c) 2026 Zhambyl Yermagambet
"""Exercise ordered raw processing with immutable source content."""

from baqylau_extension_api.models.content import ContentBundle, decode_base64, encode_content
from baqylau_extension_api.models.raw_transforms import RawTransformResult
from baqylau_extension_api.models.transforms import Drop, Replace
from baqylau_extension_api.processing.raw import apply_raw_transform

from tests.extension_api import raw_samples


def test_empty_result_preserves_source_bytes() -> None:
    """Keep the original source and content without a callback."""
    request = raw_samples.raw_request()
    output = apply_raw_transform(request, RawTransformResult())
    assert output == request
    blob = output.content_snapshot.resolve(output.inputs[0].content)
    assert decode_base64(blob.base64_text) == b"hello"


def test_replacement_changes_only_derived_content() -> None:
    """Return new translator bytes while keeping the recorded request intact."""
    request = raw_samples.raw_request()
    changed = encode_content(b"new\x00\xff", "text/plain")
    output = apply_raw_transform(request, RawTransformResult(
        operations=(Replace(
            input_id="raw-1", document=request.inputs[0].model_copy(update={"content": changed.reference}),
        ),),
        content_snapshot=ContentBundle(blobs=(changed,)),
    ))
    assert output.inputs[0].content == changed.reference
    assert output.content_snapshot.blobs == (changed,)
    assert request == raw_samples.raw_request()


def test_drop_all_prunes_translation_content() -> None:
    """Keep source storage out of scope while suppressing derived work."""
    request = raw_samples.raw_request()
    output = apply_raw_transform(request, RawTransformResult(operations=(
        Drop(input_id="raw-1", reason="hidden by the test"),
    )))
    assert not output.inputs and not output.content_snapshot.blobs
    assert output.context == request.context
    assert request.inputs and request.content_snapshot.blobs


def test_additions_survive_a_dropped_anchor() -> None:
    """Apply input order and stable before/after order once."""
    first = raw_samples.insertion("first").model_copy(update={"position": "before"})
    second = raw_samples.insertion("second")
    output = apply_raw_transform(raw_samples.raw_request(), RawTransformResult(operations=(
        second, Drop(input_id="raw-1", reason="replaced by additions"), first,
    )))
    assert output.inputs == (first.document, second.document)
    assert len(output.content_snapshot.blobs) == 1


def test_wire_round_trip_preserves_binary_input() -> None:
    """Validate bytes before and after process serialization."""
    request = raw_samples.raw_request()
    response = RawTransformResult(operations=(raw_samples.insertion(),))
    assert apply_raw_transform(request, response) == apply_raw_transform(
        type(request).model_validate_json(request.model_dump_json()),
        RawTransformResult.model_validate_json(response.model_dump_json()),
    )
