# Copyright (c) 2026 Zhambyl Yermagambet
"""Check immutable source bytes and decoder state before feature translation."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.content import ContentBundle
from baqylau_extension_api.schemas import SchemaSet
from baqylau_extension_api.translation import inputs
from pydantic import ValidationError

from tests.extension_api import samples, source_samples, translation_samples


def test_translation_uses_captured_source_bytes() -> None:
    """Decode only bytes carried by the request's immutable content snapshot."""
    manifest = source_samples.manifest()
    request = translation_samples.request()
    assert inputs.validate_translation_request(manifest, SchemaSet(manifest.schemas), request) == request
    assert inputs.input_document(request, request.inputs[0]).json_text == '"message-1"'


@pytest.mark.parametrize("change", [
    {"origin": "harness"}, {"owner": "peer"}, {"scope": samples.SESSION}, {"source_type": "peer.source"},
])
def test_translation_checks_source_origin(change: dict[str, object]) -> None:
    """Reject a raw input assigned to another owner, decoder, or scope."""
    manifest = source_samples.manifest()
    request = translation_samples.request()
    original = request.inputs[0]
    changed = original.model_copy(update={"source": original.source.model_copy(update=change)})
    request = request.model_copy(update={"inputs": (changed,)})
    with pytest.raises(ExtensionContractError):
        inputs.validate_translation_request(manifest, SchemaSet(manifest.schemas), request)


@pytest.mark.parametrize("encoded", [b"42", b"not-json", b"\xff"])
def test_translation_validates_source_document(encoded: bytes) -> None:
    """Reject invalid schema content, invalid JSON, and invalid UTF-8."""
    manifest = source_samples.manifest()
    with pytest.raises((ExtensionContractError, ValidationError)):
        inputs.validate_translation_request(manifest, SchemaSet(manifest.schemas), translation_samples.request(encoded))


def test_translation_requires_exact_content() -> None:
    """Reject references not present in the immutable snapshot."""
    manifest = source_samples.manifest()
    request = translation_samples.request().model_copy(update={"content_snapshot": ContentBundle()})
    with pytest.raises(ExtensionContractError, match="not present"):
        inputs.validate_translation_request(manifest, SchemaSet(manifest.schemas), request)


def test_translation_rejects_repeated_input_ids() -> None:
    """Require one unambiguous decision target for each recorded input."""
    manifest = source_samples.manifest()
    request = translation_samples.request()
    repeated = (*request.inputs, *request.inputs)
    request = request.model_copy(update={"inputs": repeated})
    with pytest.raises(ExtensionContractError, match="translation input identities"):
        inputs.validate_translation_request(manifest, SchemaSet(manifest.schemas), request)


def test_translation_state_cannot_claim_a_peer() -> None:
    """Keep captured decoder state inside the package's schema owner."""
    manifest = source_samples.manifest()
    request = translation_samples.request()
    changed = request.state.model_copy(update={"document": samples.encoded_document()})
    request = request.model_copy(update={"state": changed})
    with pytest.raises(ExtensionContractError):
        inputs.validate_translation_request(manifest, SchemaSet(manifest.schemas), request)


def test_translation_requires_unique_causes() -> None:
    """Reject ambiguous original cause metadata before it reaches the decoder."""
    manifest = source_samples.manifest()
    request = translation_samples.request()
    source = request.inputs[0].model_copy(update={"causes": ("cause-1", "cause-1")})
    request = request.model_copy(update={"inputs": (source,)})
    with pytest.raises(ExtensionContractError, match="cause identities"):
        inputs.validate_translation_request(manifest, SchemaSet(manifest.schemas), request)
