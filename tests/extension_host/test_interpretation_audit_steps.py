# Copyright (c) 2026 Zhambyl Yermagambet
"""Check bounded audit reads of hand-built stored steps."""

from pathlib import Path

from app.raw_event_audit_documents import audit_document
from tests.extension_host import (
    interpretation_audit_steps_fixture as fixture,
    interpretation_large_fixture as large_fixture,
)

BOUNDED_AUDIT_BYTES = 16_384


def test_audit_reports_operation_kinds(tmp_path: Path) -> None:
    """An applied reply reports its operation kinds without its documents."""
    applied = fixture.applied_step()
    case = fixture.step_case(tmp_path, applied)

    assert case.audit_steps()[0].operation_kinds == ("keep", "drop")


def test_audit_reports_rejection_evidence(tmp_path: Path) -> None:
    """A rejected reply keeps its exact size, digest, and diagnostic code."""
    evidence = fixture.rejected_evidence()
    case = fixture.step_case(tmp_path, evidence.step)
    observed = case.audit_steps()[0]

    assert observed.stage == evidence.step.stage
    assert (observed.observed_byte_length, observed.observed_digest) == (evidence.byte_length, evidence.digest)
    assert observed.diagnostic_code == evidence.diagnostic_code


def test_audit_stays_bounded_for_a_large_journal(tmp_path: Path) -> None:
    """A large stored body never enters the bounded audit read."""
    step = large_fixture.large_applied_step()
    case = fixture.step_case(tmp_path, step)
    document = audit_document(case.audit())
    document_bytes = len(document.model_dump_json().encode("utf-8"))

    assert len(document.steps) == 1
    assert document.steps[0].operation_kinds == ("insert",)
    assert fixture.stored_body_bytes(case) > document_bytes
    assert document_bytes < BOUNDED_AUDIT_BYTES
