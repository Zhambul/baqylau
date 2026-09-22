# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep core activity, lifecycle, and old complete-pass steps by reference."""

from typing import Annotated, Literal

from baqylau_extension_api.models import base, canonical, transforms
from pydantic import Field

from domain.records import RecordedTranslationDecision
from extensions.models import interpretation_steps as steps
from extensions.models.interpretation_bodies import (
    CANONICAL_FACT_KIND,
    CONTENT_BUNDLE_KIND,
    RAW_INPUT_KIND,
    BodyRef,
    BodyResolver,
    BodyStore,
)


class StoredTranslatedCoreInput(base.WireModel):
    """Keep checked core output with referenced selected activity bytes."""

    source: BodyRef
    content_snapshot: BodyRef
    translator_version: base.NonemptyText
    decision: RecordedTranslationDecision
    reason: base.NonemptyText | None = None
    facts: Annotated[tuple[BodyRef, ...], Field(max_length=transforms.MAX_TRANSFORM_OUTPUTS)] = ()


class StoredCoreTranslationStep(StoredTranslatedCoreInput):
    """Read an old complete core pass without treating it as current activity."""

    stage: Literal["core_translation"] = "core_translation"


class StoredCoreActivityStep(StoredTranslatedCoreInput):
    """Record only activity from a surviving or added raw input."""

    stage: Literal["core_activity"] = "core_activity"


class StoredCoreLifecycleStep(base.WireModel):
    """Bind referenced required state to stored original bytes."""

    stage: Literal["core_lifecycle"] = "core_lifecycle"
    content_byte_length: base.Revision
    content_digest: base.Digest
    translator_version: base.NonemptyText
    decision: RecordedTranslationDecision
    reason: base.NonemptyText | None = None
    facts: Annotated[tuple[BodyRef, ...], Field(max_length=transforms.MAX_TRANSFORM_OUTPUTS)] = ()


def _store_core_step(
    step: steps.InterpretationStep, store: BodyStore,
) -> StoredCoreTranslationStep | StoredCoreActivityStep | StoredCoreLifecycleStep | None:
    if isinstance(step, steps.CoreTranslationStep):
        return StoredCoreTranslationStep(
            source=store.intern(step.source, RAW_INPUT_KIND),
            content_snapshot=store.intern(step.content_snapshot, CONTENT_BUNDLE_KIND),
            translator_version=step.translator_version, decision=step.decision, reason=step.reason,
            facts=tuple(store.intern(fact, CANONICAL_FACT_KIND) for fact in step.facts),
        )
    if isinstance(step, steps.CoreActivityStep):
        return StoredCoreActivityStep(
            source=store.intern(step.source, RAW_INPUT_KIND),
            content_snapshot=store.intern(step.content_snapshot, CONTENT_BUNDLE_KIND),
            translator_version=step.translator_version, decision=step.decision, reason=step.reason,
            facts=tuple(store.intern(fact, CANONICAL_FACT_KIND) for fact in step.facts),
        )
    if isinstance(step, steps.CoreLifecycleStep):
        return StoredCoreLifecycleStep(
            content_byte_length=step.content_byte_length, content_digest=step.content_digest,
            translator_version=step.translator_version, decision=step.decision, reason=step.reason,
            facts=tuple(store.intern(fact, CANONICAL_FACT_KIND) for fact in step.facts),
        )
    return None


def _load_core_step(
    stored: StoredCoreTranslationStep | StoredCoreActivityStep | StoredCoreLifecycleStep, resolver: BodyResolver,
) -> steps.InterpretationStep:
    if isinstance(stored, StoredCoreTranslationStep):
        return steps.CoreTranslationStep(
            source=resolver.resolve_raw_input(stored.source),
            content_snapshot=resolver.resolve_content(stored.content_snapshot),
            translator_version=stored.translator_version, decision=stored.decision, reason=stored.reason,
            facts=_load_core_facts(stored.facts, resolver),
        )
    if isinstance(stored, StoredCoreActivityStep):
        return steps.CoreActivityStep(
            source=resolver.resolve_raw_input(stored.source),
            content_snapshot=resolver.resolve_content(stored.content_snapshot),
            translator_version=stored.translator_version, decision=stored.decision, reason=stored.reason,
            facts=_load_core_facts(stored.facts, resolver),
        )
    return steps.CoreLifecycleStep(
        content_byte_length=stored.content_byte_length, content_digest=stored.content_digest,
        translator_version=stored.translator_version, decision=stored.decision, reason=stored.reason,
        facts=_load_core_facts(stored.facts, resolver),
    )


def _load_core_facts(refs: tuple[BodyRef, ...], resolver: BodyResolver) -> tuple[canonical.CoreFact, ...]:
    """Restore referenced required or activity facts and reject a foreign branch.

    Returns:
        The exact core facts.

    Raises:
        TypeError: If one reference names an extension fact.

    """
    facts: list[canonical.CoreFact] = []
    for ref in refs:
        fact = resolver.resolve_fact(ref)
        if not isinstance(fact, canonical.CoreFact):
            message = "core step references an extension fact"
            raise TypeError(message)
        facts.append(fact)
    return tuple(facts)
