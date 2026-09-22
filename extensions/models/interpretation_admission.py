# Copyright (c) 2026 Zhambyl Yermagambet
"""Admit each normalized journal step before a call and before its result."""

import hashlib

from baqylau_extension_api.models import documents
from baqylau_extension_api.models.base import ExtensionId, OpaqueId, WireModel

from extensions.models import interpretation_steps as steps
from extensions.models.interpretation_bodies import BodyStore
from extensions.models.interpretation_storage_steps import StoredInterpretationStep, store_step
from extensions.models.interpretations import MAX_INTERPRETATION_BYTES, InterpretationProposal

ADMISSION_RESERVE_BYTES = 1_048_576
REJECTION_CODE = "journal_limit"
REJECTION_MESSAGE = "The reply exceeds the journal budget"


class JournalAdmission:
    """Measure the normalized journal before a call and before result application.

    The tracker keeps one body store and the stored step documents. A rejected
    step restores the store to its checkpoint, so the preceding journal is
    unchanged. The reserve holds the journal header, the final fact references,
    and one failure record.
    """

    def __init__(self, limit: int | None = None) -> None:
        """Start an empty journal at the selected limit."""
        self._limit = MAX_INTERPRETATION_BYTES if limit is None else limit
        self._store = BodyStore()
        self._steps: list[StoredInterpretationStep] = []
        self._step_bytes = 0

    def can_call(self) -> bool:
        """Return whether the remaining budget can hold another call record.

        Returns:
            True when the reserve still has room.

        """
        return self.remaining() > 0

    def remaining(self) -> int:
        """Count the bytes left for calls after the reserve.

        Returns:
            The remaining admission budget.

        """
        return max(0, self._limit - ADMISSION_RESERVE_BYTES - self.used())

    def used(self) -> int:
        """Count the admitted step metadata and distinct body bytes.

        Returns:
            The used admission budget.

        """
        return self._step_bytes + self._store.byte_length()

    def admit(self, step: steps.InterpretationStep) -> StoredInterpretationStep | None:
        """Admit one normalized call step from the call budget.

        Returns:
            The stored step, or None when the journal limit rejects it.

        """
        return self._admit(step, self._limit - ADMISSION_RESERVE_BYTES)

    def admit_reserved(self, step: steps.InterpretationStep) -> StoredInterpretationStep | None:
        """Admit one control record from the reserved budget.

        Returns:
            The stored step, or None when even the reserve rejects it.

        """
        return self._admit(step, self._limit)

    def _admit(self, step: steps.InterpretationStep, budget: int) -> StoredInterpretationStep | None:
        checkpoint = self._store.checkpoint()
        stored = store_step(step, self._store)
        growth = len(stored.model_dump_json().encode("utf-8"))
        if self.used() + growth > budget:
            self._store.rollback(checkpoint)
            return None
        self._steps.append(stored)
        self._step_bytes += growth
        return stored


def limited_step(
    admission: JournalAdmission, call_stage: steps.LimitedCallStage, extension_id: ExtensionId,
    input_ids: tuple[OpaqueId, ...],
) -> steps.LimitStep:
    """Record one selected call that the journal limit did not allow.

    Returns:
        The admitted limit record.

    Raises:
        ValueError: If even the limit record cannot enter the journal.

    """
    step = steps.LimitStep(call_stage=call_stage, extension_id=extension_id, input_ids=input_ids)
    if admission.admit_reserved(step) is None:
        message = "journal cannot retain its own limit record"
        raise ValueError(message)
    return step


def rejected_outcome(reply: WireModel) -> steps.RejectedStep:
    """Record one received reply that the journal cannot retain.

    Returns:
        The explicit size, digest, and reason without the reply body.

    """
    encoded = reply.model_dump_json().encode("utf-8")
    return steps.RejectedStep(
        diagnostic=documents.Diagnostic(code=REJECTION_CODE, message=REJECTION_MESSAGE),
        observed_byte_length=len(encoded),
        observed_digest=hashlib.sha256(encoded).hexdigest(),
    )


def validate_admission(proposal: InterpretationProposal) -> None:
    """Replay every step and reject a false limit or rejection claim.

    Raises:
        ValueError: If the journal exceeds its limit or a recorded claim is false.

    """
    admission = JournalAdmission()
    for step in proposal.steps:
        if isinstance(step, steps.LimitStep) and admission.can_call():
            message = "limit step claims a full journal with room remaining"
            raise ValueError(message)
        if _rejected_claim_is_false(step, admission):
            message = "rejected step claims a size that the journal could admit"
            raise ValueError(message)
        _require_total(admission, step)


def _rejected_claim_is_false(step: steps.InterpretationStep, admission: JournalAdmission) -> bool:
    if not isinstance(step, steps.RawTransformStep | steps.ExtensionTranslationStep | steps.CanonicalTransformStep):
        return False
    outcome = step.outcome
    return isinstance(outcome, steps.RejectedStep) and admission.remaining() >= outcome.observed_byte_length


def _require_total(admission: JournalAdmission, step: steps.InterpretationStep) -> None:
    if admission.admit_reserved(step) is None:
        message = "interpretation journal exceeds its normalized size limit"
        raise ValueError(message)
