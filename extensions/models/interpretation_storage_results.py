# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep extension translation decisions and their referenced next state."""

from typing import Annotated, Literal

from baqylau_extension_api.models import base, transforms
from baqylau_extension_api.models.translation_results import (
    FailedInput,
    IgnoredInput,
    TranslatedFact,
    UnsupportedInput,
)
from pydantic import Field

from extensions.models.interpretation_bodies import (
    CANONICAL_FACT_KIND,
    BodyRef,
    BodyResolver,
    BodyStore,
)
from extensions.models.interpretation_storage_context import StoredProcessingContext


class StoredTranslatedFact(base.WireModel):
    """Name one logical fact and its referenced body."""

    fact_key: base.OpaqueId
    fact: BodyRef


class StoredTranslatedInput(base.WireModel):
    """Record one or more referenced candidate facts in feature-selected order."""

    verdict: Literal["translated"] = "translated"
    input_id: base.OpaqueId
    facts: Annotated[tuple[StoredTranslatedFact, ...], Field(min_length=1, max_length=transforms.MAX_TRANSFORM_OUTPUTS)]


type StoredTranslationDecision = Annotated[
    StoredTranslatedInput | IgnoredInput | UnsupportedInput | FailedInput,
    Field(discriminator="verdict"),
]


class StoredExtensionTranslationResult(base.WireModel):
    """Propose referenced decisions and referenced next decoder state."""

    context: StoredProcessingContext
    state_revision: base.Revision
    decisions: Annotated[tuple[StoredTranslationDecision, ...], Field(max_length=1000)]
    next_state: BodyRef | None = None


def _store_translated_fact(fact: TranslatedFact, store: BodyStore) -> StoredTranslatedFact:
    return StoredTranslatedFact(fact_key=fact.fact_key, fact=store.intern(fact.fact, CANONICAL_FACT_KIND))


def _load_translated_fact(fact: StoredTranslatedFact, resolver: BodyResolver) -> TranslatedFact:
    return TranslatedFact(fact_key=fact.fact_key, fact=resolver.resolve_fact(fact.fact))
