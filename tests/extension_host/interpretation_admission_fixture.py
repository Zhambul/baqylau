# Copyright (c) 2026 Zhambyl Yermagambet
"""Build exact journal limits and large-reply evidence for admission tests."""

import pytest
from baqylau_extension_api.models import transforms

from extensions.models import interpretation_admission, interpretation_steps as steps, interpretations
from repository.impl.sqlite import interpretation_journal_writes
from tests.extension_host import (
    interpretation_normalization_fixture as evidence,
    processing_pipeline_fixture as pipeline,
)

# The reply builders live beside their own budgets; keep the fixture API stable.
# isort: split

from tests.extension_host.interpretation_admission_replies import (
    large_canonical_reply as large_canonical_reply,
    large_raw_reply as large_raw_reply,
)

RESERVE_BUDGET = interpretation_admission.ADMISSION_RESERVE_BYTES
REPLY_BUDGET = 2 * RESERVE_BUDGET
SMALL_ACTIVITY_BYTES = 65_536
SMALL_BUDGET = RESERVE_BUDGET + SMALL_ACTIVITY_BYTES
DIGEST_LENGTH = 64
FAKE_DIGEST = "a" * DIGEST_LENGTH


def select_limit(monkeypatch: pytest.MonkeyPatch, limit: int) -> int:
    """Use one exact journal limit in the pipeline, the write, and the replay.

    Returns:
        The selected limit.

    """
    monkeypatch.setattr(interpretation_admission, "MAX_INTERPRETATION_BYTES", limit)
    monkeypatch.setattr(interpretation_journal_writes, "MAX_INTERPRETATION_BYTES", limit)
    return limit


def raw_step_budget(case: pipeline.PipelineCase) -> int:
    """Return the journal limit which leaves no room after the stored raw step.

    Returns:
        The reserve plus the exact normalized raw step size.

    """
    commit = stored_commit(case)
    recorded = (step for step in commit.proposal.steps if isinstance(step, steps.RawTransformStep))
    raw = next(recorded)
    measured = interpretation_admission.JournalAdmission()
    assert measured.admit(raw) is not None
    return measured.used() + interpretation_admission.ADMISSION_RESERVE_BYTES


def stored_commit(case: pipeline.PipelineCase) -> interpretations.InterpretationCommit:
    """Read the stored interpretation of one pipeline case.

    Returns:
        The stored commit.

    Raises:
        ValueError: If the case has no stored journal.

    """
    commit = case.original.store.find_interpretation(
        evidence.DEFAULT_HISTORY, case.stored.observation.raw_event_id,
    )
    if commit is None:
        message = "pipeline case has no stored journal"
        raise ValueError(message)
    return commit


def limit_stages(commit: interpretations.InterpretationCommit) -> tuple[str, ...]:
    """Read the call stages of one proposal's limit records.

    Returns:
        The limited call stages in order.

    """
    limited = (step for step in commit.proposal.steps if isinstance(step, steps.LimitStep))
    return tuple(step.call_stage for step in limited)


def rejection_evidence(case: pipeline.PipelineCase) -> tuple[steps.RejectedStep, bytes]:
    """Read the rejected outcome and the exact received reply bytes.

    Returns:
        The rejection record and the encoded reply it refused.

    """
    commit = stored_commit(case)
    reply = large_canonical_reply(evidence.canonical_request(case))
    outcome = next(
        step.outcome for step in commit.proposal.steps
        if isinstance(step, steps.CanonicalTransformStep) and isinstance(step.outcome, steps.RejectedStep)
    )
    return outcome, reply.model_dump_json().encode("utf-8")


def rejected_fact_ids(case: pipeline.PipelineCase) -> frozenset[str]:
    """Read the identities of the reply that was not applied.

    Returns:
        The rejected addition identities.

    """
    reply = large_canonical_reply(evidence.canonical_request(case))
    return frozenset(
        operation.document.event_id
        for operation in reply.operations
        if isinstance(operation, transforms.Insert)
    )
