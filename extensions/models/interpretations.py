# Copyright (c) 2026 Zhambyl Yermagambet
"""Bind one complete mixed interpretation to its stored input and runtime."""

from typing import Annotated, Literal, Self

from baqylau_extension_api.models import base, canonical, documents, scopes, transforms
from pydantic import Field, model_validator

from domain.ids import RawEventId
from domain.records import RecordedTranslationDecision
from extensions.models.interpretation_steps import InterpretationStep

MAX_INTERPRETATION_STEPS = 1000
MAX_INTERPRETATION_BYTES = 33_554_432
CURRENT_INTERPRETATION_FORMAT: Literal[2] = 2


class InterpretationBinding(base.WireModel):
    """Reject late runtimes and results based on a changed canonical boundary."""

    manager_id: base.Identifier
    runtime_revision: base.Identifier
    history_revision: base.Identifier
    raw_event_id: Annotated[RawEventId, Field(min_length=1, max_length=base.MAX_TEXT_LENGTH)]
    input_cursor: Annotated[int, Field(ge=1)]
    scope: scopes.ExtensionScope
    expected_canonical_cursor: base.Revision
    mode: Literal["live", "replay"] = "live"


class InterpretationProposal(base.WireModel):
    """Keep the final proposal and every earlier step until one atomic commit."""

    binding: InterpretationBinding
    translator_version: base.NonemptyText
    decision: RecordedTranslationDecision
    reason: base.NonemptyText | None = None
    facts: Annotated[tuple[canonical.CanonicalFact, ...], Field(max_length=transforms.MAX_TRANSFORM_OUTPUTS)] = ()
    steps: Annotated[tuple[InterpretationStep, ...], Field(max_length=MAX_INTERPRETATION_STEPS)] = ()
    format_version: Literal[1, 2] = 1

    @model_validator(mode="after")
    def validate_proposal(self) -> Self:
        """Reject repeated final IDs, foreign scopes, and invalid verdicts.

        The normalized journal boundary admits exact write size. This model
        check does not measure the expanded JSON, because shared bodies can
        repeat in it without repeating in storage.

        Returns:
            The complete immutable proposal.

        Raises:
            ValueError: If the proposal is inconsistent.

        """
        _validate_facts(self)
        if bool(self.facts) != (self.decision == RecordedTranslationDecision.TRANSLATED):
            message = "only a translated interpretation can have final facts"
            raise ValueError(message)
        if self.decision == RecordedTranslationDecision.SUPPRESSED and self.reason is None:
            message = "a suppressed interpretation requires its reason"
            raise ValueError(message)
        return self


class StoredCanonicalFact(canonical.CommittedFact):
    """Read either strict fact branch at an explicit history revision."""

    history_revision: base.Identifier


class ExtensionFactMetadata(base.WireModel):
    """Store the extension scope and schema once, separate from its exact document text."""

    scope: scopes.ExtensionScope
    schema_ref: documents.SchemaRef
    causes: tuple[base.OpaqueId, ...]


class InterpretationCommit(base.WireModel):
    """Supply host completion time separately from the stable interpretation proposal."""

    proposal: InterpretationProposal
    completed_at: float


class InterpretationOutcome(base.WireModel):
    """Separate new accepted facts from convergence and an exact request retry."""

    accepted: tuple[StoredCanonicalFact, ...] = ()
    deduplicated: tuple[StoredCanonicalFact, ...] = ()
    repeated: bool = False


def _validate_facts(proposal: InterpretationProposal) -> None:
    if len({fact.event_id for fact in proposal.facts}) != len(proposal.facts):
        message = "interpretation final fact identities must be unique"
        raise ValueError(message)
    if any(fact.scope != proposal.binding.scope for fact in proposal.facts):
        message = "interpretation facts must retain the original scope"
        raise ValueError(message)
