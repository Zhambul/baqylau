# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep complete translation decisions and first candidate bodies in order."""

from collections.abc import Iterator

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest import rules
from baqylau_extension_api.models.canonical import CanonicalFact
from baqylau_extension_api.models.transforms import MAX_TRANSFORM_OUTPUTS
from baqylau_extension_api.models.translation_inputs import ExtensionTranslationRequest
from baqylau_extension_api.models.translation_results import ExtensionTranslationResult, TranslatedInput

MAX_TRANSLATION_RESPONSE_BYTES = 4_194_304


def validate_translation_result(
    request: ExtensionTranslationRequest, response: ExtensionTranslationResult,
) -> ExtensionTranslationResult:
    """Reject stale state and incomplete decisions before accepting any result.

    Returns:
        A complete ordered proposal with no host cursor or state mutation.

    Raises:
        ExtensionContractError: If context, state, decision order, or bounds differ.

    """
    checked_request = ExtensionTranslationRequest.model_validate(request)
    checked = ExtensionTranslationResult.model_validate(response)
    if checked.context != checked_request.context or checked.state_revision != checked_request.state.revision:
        message = "translation result changed its processing context or state revision"
        raise ExtensionContractError(message)
    expected = tuple(source.source.input_id for source in checked_request.inputs)
    if tuple(decision.input_id for decision in checked.decisions) != expected:
        message = "translation decisions must cover each input once in input order"
        raise ExtensionContractError(message)
    if len(checked.model_dump_json().encode("utf-8")) > MAX_TRANSLATION_RESPONSE_BYTES:
        message = "translation result exceeds its encoded size limit"
        raise ExtensionContractError(message)
    _validate_fact_count(checked)
    return checked


def translated_candidates(response: ExtensionTranslationResult) -> tuple[CanonicalFact, ...]:
    """Retain the first candidate body for each logical fact in the batch.

    The complete response still contains every source proposal for audit and
    source links. The host must store it with the accepted interpretation.

    Returns:
        Unique canonical input in first-observed order.

    """
    identities: set[str] = set()
    candidates: list[CanonicalFact] = []
    for fact in _facts(response):
        if fact.event_id not in identities:
            identities.add(fact.event_id)
            candidates.append(fact)
    return tuple(candidates)


def _validate_fact_count(response: ExtensionTranslationResult) -> None:
    count = 0
    for decision in response.decisions:
        if isinstance(decision, TranslatedInput):
            rules.require_unique((output.fact_key for output in decision.facts), "translated fact keys per input")
            count += len(decision.facts)
    if count > MAX_TRANSFORM_OUTPUTS:
        message = "translation result exceeds its total fact limit"
        raise ExtensionContractError(message)


def _facts(response: ExtensionTranslationResult) -> Iterator[CanonicalFact]:
    for decision in response.decisions:
        if isinstance(decision, TranslatedInput):
            for output in decision.facts:
                yield output.fact
