# Copyright (c) 2026 Zhambyl Yermagambet
"""Check prior-state coverage inside the complete interpretation transaction."""

from pathlib import Path

import pytest
from baqylau_extension_api.models import canonical, scopes

from extensions.models.interpretations import InterpretationCommit
from tests import sqlite_migration_fixture as database_snapshots
from tests.extension_host import (
    interpretation_fixture as fixture,
    interpretation_prior as prior,
    interpretation_snapshot_fixture as snapshots,
    interpretation_transforms as operations,
)


def test_complete_captured_state_is_accepted(tmp_path: Path) -> None:
    """The repository accepts its own snapshot and preserves it through retries."""
    case = fixture.installed(tmp_path, operations.manifest())
    request = prior.proposal(case)
    selected = snapshots.request(case.store, request.proposal.binding.scope)
    captured = case.store.capture_prior_state(selected)
    request = prior.with_snapshot(request, captured)
    assert captured.complete and len(captured.facts) == 1
    assert len(case.store.record_interpretation(request).deduplicated) == 1
    assert case.store.find_interpretation("default", request.proposal.binding.raw_event_id) == request
    assert case.store.record_interpretation(request).repeated


@pytest.mark.parametrize("complete", [False, True])
@pytest.mark.parametrize("failed", [False, True])
def test_omitted_facts_need_incomplete_state(tmp_path: Path, *, complete: bool, failed: bool) -> None:
    """A false completeness claim must not change any part of the database."""
    case = fixture.installed(tmp_path, operations.manifest())
    request = prior.proposal(case)
    request = prior.with_snapshot(request, canonical.CoreStateSnapshot(
        after_cursor=request.proposal.binding.expected_canonical_cursor, complete=complete,
    ), failed=failed)
    if complete:
        before = database_snapshots.snapshot(case.store.database)
        with pytest.raises(ValueError, match="complete prior snapshot omits facts"):
            case.store.record_interpretation(request)
        assert database_snapshots.snapshot(case.store.database) == before
    else:
        assert len(case.store.record_interpretation(request).deduplicated) == 1


def test_complete_claim_still_checks_bodies(tmp_path: Path) -> None:
    """Matching count and identity cannot authorize a changed acceptance time."""
    case = fixture.installed(tmp_path, operations.manifest())
    request = prior.proposal(case)
    selected = prior.prior_fact(request).model_copy(update={"accepted_at": 999.0})
    request = prior.with_snapshot(request, canonical.CoreStateSnapshot(
        after_cursor=request.proposal.binding.expected_canonical_cursor, facts=(selected,), complete=True,
    ))
    before = database_snapshots.snapshot(case.store.database)
    with pytest.raises(ValueError, match="different prior fact snapshot"):
        case.store.record_interpretation(request)
    assert database_snapshots.snapshot(case.store.database) == before


def test_complete_claim_excludes_other_scopes(tmp_path: Path) -> None:
    """A fact in another scope does not make this scope's complete claim false."""
    case = fixture.installed(tmp_path, operations.manifest())
    snapshots.seed(case.store, (snapshots.fact("other-scope", scopes.RepositoryScope(
        repository_id="project", worktree="/project", git_directory="/project/.git",
    )),))
    request = prior.proposal(case)
    selected = snapshots.request(case.store, request.proposal.binding.scope)
    captured = case.store.capture_prior_state(selected)
    request = prior.with_snapshot(request, captured)
    assert captured.complete and len(captured.facts) == 1
    assert len(case.store.record_interpretation(request).deduplicated) == 1


def test_old_journal_has_no_complete_claim(tmp_path: Path) -> None:
    """An older journal without the new field still decodes and accepts."""
    case = fixture.installed(tmp_path, operations.manifest())
    request = prior.proposal(case)
    encoded = request.model_dump_json().replace(',"complete":false', "")
    decoded = InterpretationCommit.model_validate_json(encoded)
    assert decoded == request
    assert len(case.store.record_interpretation(decoded).deduplicated) == 1


def test_complete_claim_excludes_other_histories(tmp_path: Path) -> None:
    """Candidate facts do not enter the live completeness check."""
    case = fixture.installed(tmp_path, operations.manifest())
    snapshots.seed(case.store, (snapshots.fact("candidate"),), "candidate")
    request = prior.proposal(case)
    selected = snapshots.request(case.store, request.proposal.binding.scope)
    captured = case.store.capture_prior_state(selected)
    request = prior.with_snapshot(request, captured)
    assert captured.complete and len(captured.facts) == 1
    assert len(case.store.record_interpretation(request).deduplicated) == 1
