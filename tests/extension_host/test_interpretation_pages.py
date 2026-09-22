# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep mixed pages bounded and exact scopes separate."""

from pathlib import Path

import pytest
from baqylau_extension_api.models.scopes import ExtensionScope, RepositoryScope, SessionScope

from tests.extension_host import (
    interpretation_core as core,
    interpretation_fixture as fixtures,
    lifecycle_fixture as lifecycle,
    observation_fixture as originals,
)

REPOSITORY = RepositoryScope(repository_id="project", worktree="/project", git_directory="/project/.git")
SESSION = SessionScope(session_id="session", actor_id="actor", harness="test")
HISTORY = "default"


@pytest.mark.parametrize(("after", "limit"), [
    (-1, 1), (True, 1), (0, 0),
    (0, True), (0, 1001),
])
def test_invalid_page_bound_is_rejected(tmp_path: Path, after: int, limit: int) -> None:
    """Boolean and invalid numeric bounds cannot start either page query."""
    case = fixtures.installed(tmp_path)
    with pytest.raises(ValueError, match=r"page cursor|valid integer"):
        case.store.current_fact_page(after, limit)
    with pytest.raises(ValueError, match=r"page cursor|valid integer"):
        case.store.facts_for_scope(HISTORY, SESSION, after, limit)


@pytest.mark.parametrize(("scope", "foreign"), [
    (REPOSITORY, REPOSITORY.model_copy(update={"worktree": "/other"})),
    (REPOSITORY, REPOSITORY.model_copy(update={"git_directory": "/other/.git"})),
    (SESSION, SESSION.model_copy(update={"actor_id": "other-actor"})),
    (SESSION, SESSION.model_copy(update={"harness": "other-harness"})),
])
def test_scope_page_uses_complete_identity(
    tmp_path: Path, scope: ExtensionScope, foreign: ExtensionScope,
) -> None:
    """A display label or session ID alone cannot select another scope's fact."""
    case = fixtures.installed(tmp_path)
    request = fixtures.proposal(case, originals.select_scope(case.original.request, scope))
    accepted = case.store.record_interpretation(request).accepted
    assert case.store.facts_for_scope(HISTORY, scope, 0, 10).facts == accepted
    assert not case.store.facts_for_scope(HISTORY, foreign, 0, 10).facts


def test_small_pages_keep_mixed_order(tmp_path: Path) -> None:
    """Use the last returned cursor, not the page head, to reach both fact branches."""
    case = fixtures.installed(tmp_path)
    accepted = case.store.record_interpretation(core.mixed_proposal(case)).accepted
    first = case.store.current_fact_page(0, 1)
    second = case.store.current_fact_page(first.facts[0].cursor, 1)
    assert (*first.facts, *second.facts) == accepted
    assert first.head == second.head == accepted[-1].cursor
    assert not case.store.current_fact_page(second.facts[0].cursor, 1).facts


def test_reads_survive_runtime_removal(tmp_path: Path) -> None:
    """Stored facts and journals do not require a running owner or an active selection."""
    case = fixtures.installed(tmp_path)
    request = fixtures.proposal(case)
    accepted = case.store.record_interpretation(request).accepted
    lifecycle.commit(case.original.lifecycle, lifecycle.proposal(
        case.original.lifecycle, operation_id="removed",
    ))
    assert case.store.current_fact_page(0, 10).facts == accepted
    assert case.store.find_interpretation(HISTORY, request.proposal.binding.raw_event_id) == request
