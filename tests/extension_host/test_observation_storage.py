# Copyright (c) 2026 Zhambyl Yermagambet
"""Store extension input without a fake session and preserve mixed arrival order."""

from dataclasses import replace
from pathlib import Path

from baqylau_extension_api.models.scopes import RepositoryScope, SessionScope

from domain.ids import RawEventId
from repository.impl.sqlite import raw_event_audits, raw_events
from tests import sqlite_test_fixtures as core
from tests.extension_api import source_samples
from tests.extension_host import observation_fixture as fixtures


def test_extension_input_has_no_fake_session(tmp_path: Path) -> None:
    """The extension branch keeps its schema, scope, position, and original document."""
    case = fixtures.installed(tmp_path)
    result = case.store.append_observations(case.request)
    original = fixtures.original(result.accepted[0])
    assert original.candidate == case.request.observations[0].observation
    assert original.source_position == case.request.observations[0].position
    assert case.store.find_observation(original.raw_event_id) == result.accepted[0]
    fixtures.require_no_fake_session(case.store.database)


def test_pending_input_has_one_global_order(tmp_path: Path) -> None:
    """Core and extension records use one host cursor and one pending queue."""
    case = fixtures.installed(tmp_path)
    raw = raw_events.SqliteRawEventRepository(case.store.database)
    raw.record((core.a_raw_event("before"),))
    extension = case.store.append_observations(case.request).accepted[0]
    raw.record((core.a_raw_event("after"),))
    pending = case.store.pending_observations(10)
    assert [entry.cursor for entry in pending] == [1, 2, 3]
    assert pending[1] == extension
    assert pending[0].observation == core.a_raw_event("before")
    assert pending[2].observation == core.a_raw_event("after")
    assert case.store.pending_observations(2) == pending[:2]


def test_core_reads_never_decode_extension_input(tmp_path: Path) -> None:
    """A source identity collision cannot change a harness resume position."""
    case = fixtures.installed(tmp_path)
    raw = raw_events.SqliteRawEventRepository(case.store.database)
    source = source_samples.SOURCE_ID
    raw.record((replace(core.a_raw_event(), source_identity=source),))
    stored = case.store.append_observations(case.request).accepted[0]
    identity = RawEventId(fixtures.original(stored).raw_event_id)
    assert raw.latest_positions((source,)) == {source: "1"}
    assert len(raw.unverdicted(10)) == 1
    assert raw.find(identity) is None
    assert raw_event_audits.SqliteRawEventAuditRepository(case.store.database).audit(identity) is None


def test_repeated_input_retains_original_row(tmp_path: Path) -> None:
    """Repeated capture does not change time, bytes, identity, or queue position."""
    case = fixtures.installed(tmp_path)
    first = case.store.append_observations(case.request)
    repeated = case.store.append_observations(case.request.model_copy(update={"observed_at": 2000.0}))
    assert not repeated.accepted
    assert repeated.repeated == first.accepted
    assert case.store.pending_observations(10) == first.accepted


def test_repository_identity_includes_worktree(tmp_path: Path) -> None:
    """The same source key in two worktrees names two different original inputs."""
    case = fixtures.installed(tmp_path)
    scope = RepositoryScope(repository_id="repo", worktree="/work/one", git_directory="/git/worktrees/one")
    first = fixtures.append_in_scope(case, scope)
    other = scope.model_copy(update={"worktree": "/work/two", "git_directory": "/git/worktrees/two"})
    second = fixtures.append_in_scope(case, other)
    assert first.observation.raw_event_id != second.observation.raw_event_id
    assert case.store.observations_for_scope(scope, 0, 1) == (first,)
    assert case.store.observations_for_scope(other, 0, 1) == (second,)
    assert not case.store.observations_for_scope(scope, first.cursor, 10)


def test_scope_page_can_include_both_branches(tmp_path: Path) -> None:
    """The strict core session scope matches the same extension scope exactly."""
    case = fixtures.installed(tmp_path)
    original = core.a_raw_event()
    scope = SessionScope(session_id=original.session_id, actor_id=original.actor_id, harness=original.harness)
    raw_events.SqliteRawEventRepository(case.store.database).record((original,))
    case.store.append_observations(fixtures.select_scope(case.request, scope))
    assert case.store.observations_for_scope(scope, 0, 10) == case.store.pending_observations(10)
