# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject false admission claims before any stored journal is accepted."""

from pathlib import Path

import pytest
from baqylau_extension_api.models import documents

from domain.records import RecordedTranslationDecision
from extensions.models import interpretation_admission, interpretation_steps as steps
from tests.extension_host import (
    interpretation_admission_fixture as admission,
    interpretation_fixture as fixtures,
    interpretation_transforms as declarations,
)


def test_false_limit_claim_is_rejected(tmp_path: Path) -> None:
    """Storage rejects a limit record when the journal still had room for the call."""
    case = fixtures.installed(tmp_path, declarations.combined_manifest())
    request = fixtures.proposal(case)
    proposal = request.proposal.model_copy(update={
        "facts": (),
        "decision": RecordedTranslationDecision.TRANSLATION_FAILED,
        "reason": "The journal limit stopped this call",
        "steps": (steps.LimitStep(
            call_stage="raw", extension_id=case.original.request.extension_id, input_ids=("raw-one",),
        ),),
    })

    with pytest.raises(ValueError, match="room remaining"):
        interpretation_admission.validate_admission(proposal)


def test_false_rejection_claim_is_rejected(tmp_path: Path) -> None:
    """Storage rejects a rejection record when the observed size could be admitted."""
    case = fixtures.installed(tmp_path, declarations.manifest())
    request = fixtures.proposal(case)
    rejected = steps.RejectedStep(
        diagnostic=documents.Diagnostic(code="journal_limit", message="The reply exceeds the journal budget"),
        observed_byte_length=1,
        observed_digest=admission.FAKE_DIGEST,
    )
    proposal = request.proposal.model_copy(update={
        "steps": (
            *request.proposal.steps,
            steps.CanonicalTransformStep(request=declarations.request(request), outcome=rejected),
        ),
    })

    with pytest.raises(ValueError, match="could admit"):
        interpretation_admission.validate_admission(proposal)
