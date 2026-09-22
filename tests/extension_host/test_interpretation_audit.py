# Copyright (c) 2026 Zhambyl Yermagambet
"""Check bounded audit reads of extension steps and revisions."""

from pathlib import Path

import pytest

from app.raw_event_audit_documents import audit_document
from repository.impl.sqlite import raw_event_audit_queries, raw_event_audits
from repository.impl.sqlite.databases import main_database
from tests import sqlite_migration_events as events, sqlite_test_fixtures as raw_fixtures
from tests.extension_host import (
    interpretation_admission_fixture as admission,
    interpretation_audit_fixture as fixture,
    lifecycle_pipeline_fixture as lifecycle,
)
from tests.plugin_tests import translation_stage_fixture as native

ORIGINAL_TEXT = "original"
HOOK_NAME = "PreToolUse"


def test_audit_reports_each_recorded_step(tmp_path: Path) -> None:
    """Every journal step appears with its stage, owner, and outcome."""
    case = lifecycle.installed(tmp_path, native.claude_prompt(ORIGINAL_TEXT))
    commit = case.pipeline.run()
    expected, observed = fixture.step_summaries(commit, case.pipeline)

    assert observed == expected


def test_audit_reports_revisions(tmp_path: Path) -> None:
    """The audit carries the exact history, runtime, and format revisions."""
    case = lifecycle.installed(tmp_path, native.claude_prompt(ORIGINAL_TEXT))
    commit = case.pipeline.run()
    stored = fixture.audit(case.pipeline)
    assert stored is not None and stored.interpretation is not None
    interpretation = stored.interpretation

    assert interpretation.history_revision == fixture.DEFAULT_HISTORY
    assert interpretation.runtime_revision == commit.proposal.binding.runtime_revision
    assert interpretation.format_version == fixture.NORMALIZED_FORMAT_VERSION
    assert not interpretation.steps_truncated


def test_audit_reports_limit_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A call that the budget stopped keeps its stage, owner, and reason."""
    case = lifecycle.installed(
        tmp_path, native.claude_hook(HOOK_NAME),
        admission_limit=admission.select_limit(monkeypatch, admission.RESERVE_BUDGET),
    )
    case.pipeline.run()
    limit_steps = fixture.limit_audit_steps(case.pipeline)

    assert limit_steps
    assert all(step.reason == fixture.LIMIT_REASON and step.owner for step in limit_steps)


def test_audit_bounds_the_step_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A journal above the audit bound reports truncation without its tail."""
    case = lifecycle.installed(tmp_path, native.claude_prompt(ORIGINAL_TEXT))
    case.pipeline.run()
    monkeypatch.setattr(raw_event_audit_queries, "MAX_AUDIT_STEPS", fixture.TRUNCATED_STEP_COUNT)
    stored = fixture.audit(case.pipeline)
    assert stored is not None and stored.interpretation is not None

    assert len(stored.interpretation.steps) == fixture.TRUNCATED_STEP_COUNT
    assert stored.interpretation.steps_truncated


def test_audit_document_carries_steps(tmp_path: Path) -> None:
    """The command-line document exposes the same bounded step metadata."""
    case = lifecycle.installed(tmp_path, native.claude_prompt(ORIGINAL_TEXT))
    commit = case.pipeline.run()
    stored = fixture.audit(case.pipeline)
    assert stored is not None
    document = audit_document(stored)

    assert document.history_revision == fixture.DEFAULT_HISTORY
    assert document.format_version == fixture.NORMALIZED_FORMAT_VERSION
    assert len(document.steps) == len(commit.proposal.steps)
    assert not document.steps_truncated


def test_core_only_audit_has_no_steps(tmp_path: Path) -> None:
    """A core translation without a mixed journal keeps meaningful audit output."""
    database = main_database(str(tmp_path / "main.db"))
    events.populate(database)
    stored = raw_event_audits.SqliteRawEventAuditRepository(database).audit(
        raw_fixtures.a_raw_event().raw_event_id,
    )
    assert stored is not None and stored.interpretation is not None

    assert stored.interpretation.steps == ()
    assert not stored.interpretation.steps_truncated
