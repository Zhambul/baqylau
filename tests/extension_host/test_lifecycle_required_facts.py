# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep required final bodies equal to the original lifecycle result."""

from pathlib import Path

import pytest
from baqylau_extension_api.core.sessions import SessionStarted

from domain.records import RecordedTranslationDecision
from tests import sqlite_migration_fixture as snapshots
from tests.extension_host import (
    interpretation_core as core,
    interpretation_fixture as fixtures,
    lifecycle_journal_changes as changes,
)


def test_required_final_body_cannot_change(tmp_path: Path) -> None:
    """A final fact cannot use a new payload under its original required identity."""
    case = fixtures.installed(tmp_path)
    request = core.core_proposal(case)
    fact = changes.required(request).facts[0]
    assert isinstance(fact.payload, SessionStarted)
    changed = fact.model_copy(update={"payload": fact.payload.model_copy(update={
        "working_directory": "/changed",
    })})
    request = request.model_copy(update={"proposal": request.proposal.model_copy(update={
        "facts": (changed,),
    })})
    before = snapshots.snapshot(case.store.database)
    with pytest.raises(ValueError, match="final facts do not match"):
        case.store.record_interpretation(request)
    assert snapshots.snapshot(case.store.database) == before


def test_required_final_fact_cannot_be_dropped(tmp_path: Path) -> None:
    """An empty activity result does not justify an empty required result."""
    case = fixtures.installed(tmp_path)
    request = core.core_proposal(case)
    request = request.model_copy(update={"proposal": request.proposal.model_copy(update={
        "facts": (), "decision": RecordedTranslationDecision.IGNORED_NONSEMANTIC,
    })})
    before = snapshots.snapshot(case.store.database)
    with pytest.raises(ValueError, match="final facts do not match"):
        case.store.record_interpretation(request)
    assert snapshots.snapshot(case.store.database) == before
