# Copyright (c) 2026 Zhambyl Yermagambet
"""Check translated fact identity, owned schemas, causes, and core source links."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.translation_inputs import ExtensionTranslationRequest
from baqylau_extension_api.models.translation_results import TranslatedInput
from baqylau_extension_api.schemas import SchemaSet
from baqylau_extension_api.translation import outputs

from tests.extension_api import samples, source_samples, transform_samples, translation_samples


def test_translated_fact_matches_registration() -> None:
    """Validate the complete owned result and unchanged decoder state."""
    manifest = source_samples.manifest()
    outputs.validate_translation_documents(
        manifest, SchemaSet(manifest.schemas), translation_samples.request(), translation_samples.result(),
    )


@pytest.mark.parametrize("change", [
    {"event_id": "forged"}, {"scope": samples.SESSION}, {"event_type": "test.sample.undeclared"},
    {"document": samples.encoded_document(), "event_type": "test.reader.fact"},
])
def test_translated_fact_rejects_changed_owner(change: dict[str, object]) -> None:
    """Reject forged logical identity, changed scope, or an unowned event document."""
    manifest = source_samples.manifest()
    original = translation_samples.translated_fact()
    changed = original.model_copy(update={"fact": original.fact.model_copy(update=change)})
    response = translation_samples.result().model_copy(update={
        "decisions": (TranslatedInput(input_id="raw-1", facts=(changed,)),),
    })
    with pytest.raises(ExtensionContractError):
        outputs.validate_translation_documents(
            manifest, SchemaSet(manifest.schemas), translation_samples.request(), response,
        )


def test_translated_fact_retains_recorded_causes() -> None:
    """Do not remove the parent references carried by the original observation."""
    manifest = source_samples.manifest()
    request = translation_samples.request()
    source = request.inputs[0].model_copy(update={"causes": ("parent",)})
    request = request.model_copy(update={"inputs": (source,)})
    with pytest.raises(ExtensionContractError, match="cause references"):
        outputs.validate_translation_documents(
            manifest, SchemaSet(manifest.schemas), request, translation_samples.result(),
        )


def test_next_decoder_state_must_be_owned() -> None:
    """Reject unowned state before any candidate or next state is accepted."""
    manifest = source_samples.manifest()
    response = translation_samples.result().model_copy(update={"next_state": samples.encoded_document()})
    with pytest.raises(ExtensionContractError):
        outputs.validate_translation_documents(
            manifest, SchemaSet(manifest.schemas), translation_samples.request(), response,
        )


@pytest.mark.parametrize("raw_ids", [("original-1",), (), ("another-original",)])
def test_translated_core_fact_keeps_source(raw_ids: tuple[str, ...]) -> None:
    """Allow typed core output only with its exact session scope and original raw link."""
    request = translation_samples.session_request()
    original = translation_samples.translated_fact(request)
    core = transform_samples.core_fact().model_copy(update={
        "event_id": original.fact.event_id, "raw_event_ids": raw_ids,
    })
    changed = original.model_copy(update={"fact": core})
    decision = TranslatedInput(input_id="raw-1", facts=(changed,))
    _check_core_output(request, decision, accepted=raw_ids == ("original-1",))


def _check_core_output(
    request: ExtensionTranslationRequest, decision: TranslatedInput, *, accepted: bool,
) -> None:
    manifest = source_samples.manifest()
    response = translation_samples.result(request).model_copy(update={"decisions": (decision,)})
    if accepted:
        outputs.validate_translation_documents(manifest, SchemaSet(manifest.schemas), request, response)
    else:
        with pytest.raises(ExtensionContractError, match="raw source reference"):
            outputs.validate_translation_documents(manifest, SchemaSet(manifest.schemas), request, response)
