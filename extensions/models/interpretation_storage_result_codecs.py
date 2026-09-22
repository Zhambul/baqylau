# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep transform replies and their referenced documents."""

from baqylau_extension_api.models import transforms
from baqylau_extension_api.models.translation_results import (
    ExtensionTranslationResult,
    TranslatedInput,
    TranslationDecision,
)

from extensions.models.interpretation_bodies import (
    ENCODED_DOCUMENT_KIND,
    BodyResolver,
    BodyStore,
)
from extensions.models.interpretation_storage_context import _load_context, _store_context
from extensions.models.interpretation_storage_operations import (
    _load_canonical_operation,
    _store_canonical_operation,
)
from extensions.models.interpretation_storage_outcomes import StoredTransformResult
from extensions.models.interpretation_storage_results import (
    StoredExtensionTranslationResult,
    StoredTranslatedInput,
    StoredTranslationDecision,
    _load_translated_fact,
    _store_translated_fact,
)


def _store_translation_result(
    result: ExtensionTranslationResult, store: BodyStore,
) -> StoredExtensionTranslationResult:
    decisions: list[StoredTranslationDecision] = []
    for decision in result.decisions:
        if isinstance(decision, TranslatedInput):
            decisions.append(
                StoredTranslatedInput(
                    input_id=decision.input_id,
                    facts=tuple(_store_translated_fact(fact, store) for fact in decision.facts),
                ),
            )
        else:
            decisions.append(decision)
    return StoredExtensionTranslationResult(
        context=_store_context(result.context, store), state_revision=result.state_revision,
        decisions=tuple(decisions),
        next_state=None if result.next_state is None else store.intern(result.next_state, ENCODED_DOCUMENT_KIND),
    )


def _load_translation_result(
    result: StoredExtensionTranslationResult, resolver: BodyResolver,
) -> ExtensionTranslationResult:
    decisions: list[TranslationDecision] = []
    for decision in result.decisions:
        if isinstance(decision, StoredTranslatedInput):
            decisions.append(
                TranslatedInput(
                    input_id=decision.input_id,
                    facts=tuple(_load_translated_fact(fact, resolver) for fact in decision.facts),
                ),
            )
        else:
            decisions.append(decision)
    return ExtensionTranslationResult(
        context=_load_context(result.context, resolver), state_revision=result.state_revision,
        decisions=tuple(decisions),
        next_state=None if result.next_state is None else resolver.resolve_document(result.next_state),
    )


def _store_canonical_result(
    result: transforms.CanonicalTransformResult, store: BodyStore,
) -> StoredTransformResult:
    return StoredTransformResult(
        operations=tuple(_store_canonical_operation(operation, store) for operation in result.operations),
        diagnostics=result.diagnostics,
    )


def _load_canonical_result(
    result: StoredTransformResult, resolver: BodyResolver,
) -> transforms.CanonicalTransformResult:
    return transforms.CanonicalTransformResult(
        operations=tuple(_load_canonical_operation(operation, resolver) for operation in result.operations),
        diagnostics=result.diagnostics,
    )
