# Copyright (c) 2026 Zhambyl Yermagambet
"""Read bounded prior facts from one exact scope and history."""

from pathlib import Path

import pytest
from baqylau_extension_api.models import scopes

from extensions.models.interpretation_snapshot import MAX_PRIOR_FACTS
from tests.extension_api import samples, transform_samples
from tests.extension_host import interpretation_snapshot_fixture as fixture

REPOSITORY = scopes.RepositoryScope(repository_id="project", worktree="/project", git_directory="/project/.git")
CURSOR_FIELD = "expected_canonical_cursor"
CANDIDATE_CURSOR = 2


def test_empty_history_is_complete(tmp_path: Path) -> None:
    """A known empty history proves that this scope has no prior facts."""
    store = fixture.repository(tmp_path)
    captured = store.capture_prior_state(fixture.request(store))
    assert captured.complete
    assert captured.after_cursor == 0 and not captured.facts


def test_mixed_snapshot_preserves_metadata(tmp_path: Path) -> None:
    """Core and extension facts retain order, accepted time, and exact bodies."""
    store = fixture.repository(tmp_path)
    facts = (
        transform_samples.core_fact().model_copy(update={"raw_event_ids": ()}),
        fixture.fact("extension", samples.SESSION),
    )
    fixture.seed(store, facts)
    captured = store.capture_prior_state(fixture.request(store, samples.SESSION))
    assert captured.complete and captured.after_cursor == len(facts)
    assert tuple(prior.fact for prior in captured.facts) == facts
    assert tuple(prior.cursor for prior in captured.facts) == (1, 2)
    times = tuple(prior.accepted_at for prior in captured.facts)
    assert times == pytest.approx((1000, 1000))


@pytest.mark.parametrize(("scope", "foreign"), [
    (REPOSITORY, REPOSITORY.model_copy(update={"worktree": "/other"})),
    (REPOSITORY, REPOSITORY.model_copy(update={"git_directory": "/other/.git"})),
    (REPOSITORY, REPOSITORY.model_copy(update={"repository_id": "other-project"})),
    (samples.SESSION, samples.SESSION.model_copy(update={"actor_id": "other-actor"})),
    (samples.SESSION, samples.SESSION.model_copy(update={"harness": "other-harness"})),
    (samples.SESSION, samples.SESSION.model_copy(update={"session_id": "other-session"})),
])
def test_snapshot_uses_exact_scope(
    tmp_path: Path, scope: scopes.ExtensionScope, foreign: scopes.ExtensionScope,
) -> None:
    """A complete empty scope can share a nonzero global history head."""
    store = fixture.repository(tmp_path)
    fixture.seed(store, (fixture.fact("selected", scope),))
    selected = store.capture_prior_state(fixture.request(store, scope))
    missing = store.capture_prior_state(fixture.request(store, foreign))
    assert selected.complete and len(selected.facts) == 1
    assert missing.complete and not missing.facts
    assert missing.after_cursor == selected.after_cursor == 1


def test_snapshot_does_not_mix_histories(tmp_path: Path) -> None:
    """The same logical ID can have a different accepted body in a candidate."""
    store = fixture.repository(tmp_path)
    fixture.seed(store, (fixture.fact("same"),))
    fixture.seed(store, (fixture.fact("same", encoded='"candidate"'),), "candidate")
    live = store.capture_prior_state(fixture.request(store))
    candidate = store.capture_prior_state(fixture.request(store).model_copy(update={
        "history_revision": "candidate", CURSOR_FIELD: CANDIDATE_CURSOR,
    }))
    assert live.complete and candidate.complete
    assert live.after_cursor == 1 and candidate.after_cursor == CANDIDATE_CURSOR
    assert live.facts[0].fact != candidate.facts[0].fact


@pytest.mark.parametrize(("history", "cursor", "reason"), [
    ("missing", 0, "history is not recorded"),
    ("default", 0, "boundary is stale"),
    ("default", 2, "boundary is stale"),
])
def test_snapshot_rejects_changed_boundary(tmp_path: Path, history: str, cursor: int, reason: str) -> None:
    """An absent history or wrong head cannot produce a valid snapshot."""
    store = fixture.repository(tmp_path)
    fixture.seed(store, (fixture.fact("first"),))
    request = fixture.request(store).model_copy(update={
        "history_revision": history, CURSOR_FIELD: cursor,
    })
    with pytest.raises(ValueError, match=reason):
        store.capture_prior_state(request)


@pytest.mark.parametrize("count", [MAX_PRIOR_FACTS, MAX_PRIOR_FACTS + 1])
def test_fact_limit_reports_omitted_tail(tmp_path: Path, count: int) -> None:
    """One extra metadata row distinguishes a full prefix from complete state."""
    store = fixture.repository(tmp_path)
    facts = tuple(fixture.fact(f"fact-{index}") for index in range(count))
    fixture.seed(store, facts)
    captured = store.capture_prior_state(fixture.request(store))
    assert len(captured.facts) == MAX_PRIOR_FACTS
    assert captured.complete == (count == MAX_PRIOR_FACTS)
    assert captured.after_cursor == count
    expected = tuple(range(1, MAX_PRIOR_FACTS + 1))
    assert tuple(prior.cursor for prior in captured.facts) == expected


@pytest.mark.parametrize(("field", "limit"), [
    ("max_facts", 0), ("max_facts", True), ("max_facts", 1001),
    ("max_bytes", 127), ("max_bytes", True), ("max_bytes", 1_048_577),
    (CURSOR_FIELD, -1), (CURSOR_FIELD, True),
])
def test_invalid_capture_bounds_are_rejected(tmp_path: Path, field: str, limit: int) -> None:
    """Copied models must pass the strict public repository boundary again."""
    store = fixture.repository(tmp_path)
    request = fixture.request(store).model_copy(update={field: limit})
    with pytest.raises(ValueError, match=field):
        store.capture_prior_state(request)
