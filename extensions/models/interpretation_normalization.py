# Copyright (c) 2026 Zhambyl Yermagambet
"""Convert one logical proposal to normalized journal storage and back."""

from typing import Literal

from baqylau_extension_api.models import base

from domain.records import RecordedTranslationDecision
from extensions.models.interpretation_bodies import BodyRef, BodyResolver, BodyStore
from extensions.models.interpretation_storage_steps import (
    StoredInterpretationStep,
    load_step,
    store_step,
)
from extensions.models.interpretations import InterpretationBinding, InterpretationProposal


class NormalizedJournalHeader(base.WireModel):
    """Bind one normalized journal to its input, verdict, and final fact references."""

    codec_version: Literal[2] = 2
    binding: InterpretationBinding
    translator_version: base.NonemptyText
    decision: RecordedTranslationDecision
    reason: base.NonemptyText | None = None
    format_version: Literal[1, 2] = 1
    facts: tuple[BodyRef, ...] = ()


class NormalizedJournal(base.WireModel):
    """Keep one complete ordered journal as references and step metadata."""

    header: NormalizedJournalHeader
    steps: tuple[StoredInterpretationStep, ...] = ()


def normalize_proposal(proposal: InterpretationProposal) -> tuple[NormalizedJournal, BodyStore]:
    """Replace every repeated body with one typed reference.

    Returns:
        The stored journal and the distinct bodies it names.

    """
    store = BodyStore()
    steps = tuple(store_step(step, store) for step in proposal.steps)
    header = NormalizedJournalHeader(
        binding=proposal.binding,
        translator_version=proposal.translator_version,
        decision=proposal.decision,
        reason=proposal.reason,
        format_version=proposal.format_version,
        facts=tuple(store.intern(fact, "canonical_fact") for fact in proposal.facts),
    )
    return NormalizedJournal(header=header, steps=steps), store


def expand_journal(
    header: NormalizedJournalHeader, steps: tuple[StoredInterpretationStep, ...], store: BodyStore,
) -> InterpretationProposal:
    """Restore the complete logical proposal from its references.

    Returns:
        The exact logical proposal that was stored.

    """
    resolver = BodyResolver(store)
    return InterpretationProposal(
        format_version=header.format_version,
        binding=header.binding,
        translator_version=header.translator_version,
        decision=header.decision,
        reason=header.reason,
        facts=tuple(map(resolver.resolve_fact, header.facts)),
        steps=tuple(load_step(step, resolver) for step in steps),
    )


def normalized_byte_length(journal: NormalizedJournal, store: BodyStore) -> int:
    """Count the complete stored journal: header, step metadata, and bodies.

    Returns:
        The exact encoded byte count for admission.

    """
    header = _encoded_length(journal.header)
    steps = sum(map(_encoded_length, journal.steps))
    return header + steps + store.byte_length()


def _encoded_length(encoded: base.WireModel) -> int:
    return len(encoded.model_dump_json().encode("utf-8"))
