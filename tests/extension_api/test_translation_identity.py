# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep translated fact IDs stable and distinct from transform addition IDs."""

import pytest
from baqylau_extension_api.identities import DerivedIdentity, derived_event_id, derived_input_id
from baqylau_extension_api.translation_identity import TranslationIdentity, translated_event_id

from tests.extension_api import operation_samples, samples, source_samples, translation_samples


def identity() -> TranslationIdentity:
    """Build the exact version-one logical identity fixture.

    Returns:
        One owned key in installation scope.

    """
    return TranslationIdentity(
        extension_id=operation_samples.OWNER, scope=source_samples.context().binding.scope, fact_key="message-1",
    )


def test_translation_identity_has_fixed_encoding() -> None:
    """Detect accidental changes to the version-one stable identity algorithm."""
    digest = "a0bf63a6706c8f1f754d67c46c96a151dd8e824327b85d40aac30a1b4d7e6feb"
    assert translated_event_id(identity()) == f"translated:v1:test.sample:{digest}"


@pytest.mark.parametrize("change", [
    {"extension_id": "peer"}, {"scope": samples.SESSION}, {"fact_key": "another-key"},
])
def test_translation_identity_keeps_ownership(change: dict[str, object]) -> None:
    """Do not converge different owners, scopes, or logical facts."""
    assert translated_event_id(identity().model_copy(update=change)) != translated_event_id(identity())


def test_translation_identity_ignores_raw_row_ids() -> None:
    """Repeated observations can identify the same logical canonical fact."""
    request = translation_samples.request()
    original = translation_samples.translated_fact(request)
    changed = request.inputs[0].source.model_copy(update={"input_id": "another-raw-row"})
    source = request.inputs[0].model_copy(update={"source": changed})
    request = request.model_copy(update={"inputs": (source,)})
    assert translation_samples.translated_fact(request).fact.event_id == original.fact.event_id


@pytest.mark.parametrize("change", [
    {"runtime_revision": "new-runtime"}, {"history_revision": "new-history"},
    {"settings_revision": 5}, {"mode": "replay"},
])
def test_fact_identity_ignores_revisions(change: dict[str, object]) -> None:
    """A new interpretation can preserve the fact's logical identity."""
    request = translation_samples.request()
    request = request.model_copy(update={"context": request.context.model_copy(update=change)})
    assert translation_samples.translated_fact(request).fact.event_id == translated_event_id(identity())


def test_translation_and_addition_ids_differ() -> None:
    """Keep source translation separate from raw and canonical insertion IDs."""
    derived = DerivedIdentity(extension_id=operation_samples.OWNER, input_id="message-1", output_key="message-1")
    assert translated_event_id(identity()) not in {derived_event_id(derived), derived_input_id(derived)}
