# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep translation verdicts, context, and captured state exact."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.documents import Diagnostic
from baqylau_extension_api.models.translation_results import (
    FailedInput,
    IgnoredInput,
    TranslatedInput,
    TranslationDecision,
    UnsupportedInput,
)
from baqylau_extension_api.translation import results
from pydantic import TypeAdapter, ValidationError

from tests.extension_api import translation_samples

DIAGNOSTIC = Diagnostic(code="fixture", message="The fixture input is not translated.")
INPUT_ID = "raw-1"
DECISIONS_FIELD = "decisions"


def test_translation_verdicts_remain_distinct() -> None:
    """Keep translated, intentional ignore, unknown, and failed input distinct."""
    adapter = TypeAdapter[TranslationDecision](TranslationDecision)
    decisions: tuple[TranslationDecision, ...] = (
        translation_samples.result().decisions[0],
        IgnoredInput(input_id=INPUT_ID, reason="Known metadata only."),
        UnsupportedInput(input_id=INPUT_ID, diagnostic=DIAGNOSTIC),
        FailedInput(input_id=INPUT_ID, diagnostic=DIAGNOSTIC),
    )
    for decision in decisions:
        assert adapter.validate_json(decision.model_dump_json()) == decision


@pytest.mark.parametrize("change", [
    {"runtime_revision": "old"}, {"history_revision": "old"}, {"extension_id": "peer"},
    {"settings_revision": 1}, {"input_cursor": 2}, {"mode": "replay"},
])
def test_translation_reply_keeps_full_context(change: dict[str, object]) -> None:
    """Reject a reply from another immutable processing boundary."""
    request = translation_samples.request()
    response = translation_samples.result()
    response = response.model_copy(update={"context": request.context.model_copy(update=change)})
    with pytest.raises(ExtensionContractError, match="processing context"):
        results.validate_translation_result(request, response)


def test_translation_reply_keeps_state_revision() -> None:
    """Do not overwrite decoder state with a reply from another revision."""
    response = translation_samples.result().model_copy(update={"state_revision": 1})
    with pytest.raises(ExtensionContractError, match="state revision"):
        results.validate_translation_result(translation_samples.request(), response)


@pytest.mark.parametrize("decisions", [(), (IgnoredInput(input_id="unknown", reason="Unknown"),)])
def test_translation_requires_complete_decisions(decisions: tuple[TranslationDecision, ...]) -> None:
    """Do not silently omit input or introduce an unrecorded input decision."""
    response = translation_samples.result().model_copy(update={DECISIONS_FIELD: decisions})
    with pytest.raises(ExtensionContractError, match="cover each input once"):
        results.validate_translation_result(translation_samples.request(), response)


def test_translation_rejects_repeated_fact_keys() -> None:
    """Require a single proposal per logical key within each input decision."""
    output = translation_samples.translated_fact()
    decision = TranslatedInput(input_id=INPUT_ID, facts=(output, output))
    response = translation_samples.result().model_copy(update={DECISIONS_FIELD: (decision,)})
    with pytest.raises(ExtensionContractError, match="fact keys per input"):
        results.validate_translation_result(translation_samples.request(), response)


def test_empty_input_keeps_state_boundary() -> None:
    """Allow an all-suppressed raw batch without creating fake facts."""
    request = translation_samples.request().model_copy(update={"inputs": ()})
    response = translation_samples.result().model_copy(update={DECISIONS_FIELD: ()})
    assert results.validate_translation_result(request, response) == response
    assert results.translated_candidates(response) == ()


def test_translated_verdict_requires_a_fact() -> None:
    """Use an explicit ignored or failed verdict when no candidate exists."""
    with pytest.raises(ValidationError):
        TranslatedInput(input_id=INPUT_ID, facts=())
