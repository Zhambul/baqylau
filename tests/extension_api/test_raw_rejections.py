# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject invalid raw proposals as a complete batch."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.content import ContentBundle, encode_content
from baqylau_extension_api.models.raw_transforms import RawTransformResult
from baqylau_extension_api.models.transforms import Drop, Replace
from baqylau_extension_api.processing.raw import apply_raw_transform
from pydantic import ValidationError

from tests.extension_api import raw_samples


@pytest.mark.parametrize("field", ["input_id", "source_type", "owner"])
def test_replacement_preserves_origin(field: str) -> None:
    """Do not let a content change select another decoder or claim its identity."""
    request = raw_samples.raw_request()
    changed = request.inputs[0].model_copy(update={field: "foreign"})
    with pytest.raises(ExtensionContractError):
        apply_raw_transform(request, RawTransformResult(operations=(Replace(input_id="raw-1", document=changed),)))
    assert request == raw_samples.raw_request()


def test_unknown_anchor_rejects_complete_result() -> None:
    """Do not return a partial valid prefix when a later operation is invalid."""
    with pytest.raises(ExtensionContractError, match="unknown input"):
        apply_raw_transform(raw_samples.raw_request(), RawTransformResult(operations=(
            raw_samples.insertion(), Drop(input_id="unknown", reason="invalid test anchor"),
        )))


def test_addition_requires_derived_identity() -> None:
    """Reject an insertion that claims an arbitrary raw identity."""
    addition = raw_samples.insertion()
    changed = addition.document.model_copy(update={"input_id": "foreign"})
    invalid = addition.model_copy(update={"document": changed})
    with pytest.raises(ExtensionContractError, match="insertion identity"):
        apply_raw_transform(raw_samples.raw_request(), RawTransformResult(operations=(invalid,)))


def test_missing_input_bytes_fail_before_dispatch() -> None:
    """Require the worker snapshot to contain every original content reference."""
    invalid = raw_samples.raw_request().model_copy(update={"content_snapshot": ContentBundle()})
    with pytest.raises(ValidationError, match="supplied snapshot"):
        apply_raw_transform(invalid, RawTransformResult())


def test_replacement_requires_exact_new_bytes() -> None:
    """Do not resolve a new reference through a file or live service."""
    request = raw_samples.raw_request()
    changed = request.inputs[0].model_copy(update={
        "content": encode_content(b"missing").reference,
    })
    with pytest.raises(ExtensionContractError, match="supplied snapshot"):
        apply_raw_transform(request, RawTransformResult(operations=(Replace(input_id="raw-1", document=changed),)))


def test_unused_returned_content_is_rejected() -> None:
    """Require every returned content object to support a result input."""
    with pytest.raises(ExtensionContractError, match="unused content"):
        apply_raw_transform(raw_samples.raw_request(), RawTransformResult(
            content_snapshot=ContentBundle(blobs=(encode_content(b"unused"),)),
        ))
