"""Named application insight reads and insight checks."""

from __future__ import annotations

from pytest_bdd import parsers, then, when

from api.application.models.resume.resumable_session_response import (
    ResumableSessionResponse,
)
from sdk.client import BaqylauClient
from tests.e2e.testkit.insights import assert_completed_session_delta
from tests.e2e.testkit.references import InsightsSnapshots, ResumableLists, Sessions


def _session_row(
    resumable_lists: ResumableLists,
    sessions: Sessions,
    list_name: str,
    session_name: str,
) -> ResumableSessionResponse:
    session_id = sessions.get(session_name).session_id
    found = [
        item
        for item in resumable_lists.get(list_name)
        if item.session_id == session_id
    ]
    assert len(found) == 1, (
        f"resumable list {list_name!r} has {len(found)} rows for session "
        f"{session_name!r}"
    )
    return found[0]


@when(parsers.parse('I read application insights as "{name}"'))
def read_application_insights(
    client: BaqylauClient,
    insights_snapshots: InsightsSnapshots,
    name: str,
) -> None:
    insights_snapshots.bind(name, client.insights.state())


@when(parsers.parse('I read resumable sessions for the workspace as "{name}"'))
def read_resumable_sessions(
    client: BaqylauClient,
    workspace: str,
    resumable_lists: ResumableLists,
    name: str,
) -> None:
    resumable_lists.bind(
        name,
        client.insights.resumable_sessions(workspace=workspace),
    )


@when(parsers.parse("I search resumable sessions for '{search}' as \"{name}\""))
def search_resumable_sessions(
    client: BaqylauClient,
    workspace: str,
    resumable_lists: ResumableLists,
    search: str,
    name: str,
) -> None:
    resumable_lists.bind(
        name,
        client.insights.resumable_sessions(workspace=workspace, search=search),
    )


@when(parsers.parse(
    'I search resumable sessions for session "{session_name}" ID as "{name}"'
))
def search_resumable_sessions_by_id(
    client: BaqylauClient,
    workspace: str,
    resumable_lists: ResumableLists,
    sessions: Sessions,
    session_name: str,
    name: str,
) -> None:
    resumable_lists.bind(
        name,
        client.insights.resumable_sessions(
            workspace=workspace,
            search=str(sessions.get(session_name).session_id),
        ),
    )


@then(parsers.parse('insights "{name}" include the workspace'))
def insights_include_workspace(
    insights_snapshots: InsightsSnapshots,
    workspace: str,
    name: str,
) -> None:
    found = [
        item
        for item in insights_snapshots.get(name).projects
        if item.working_directory == workspace
    ]
    assert len(found) == 1, f"insights {name!r} have {len(found)} workspace rows"


@then(parsers.parse('insights "{name}" report at least {count:d} session'))
def insights_report_session_count(
    insights_snapshots: InsightsSnapshots,
    name: str,
    count: int,
) -> None:
    found = insights_snapshots.get(name).total_session_count
    assert found >= count, f"insights {name!r} report {found} sessions"


@then(parsers.parse(
    'insights "{after_name}" differ from "{before_name}" by exactly completed '
    'session "{session_name}"'
))
def insights_have_completed_session_delta(
    client: BaqylauClient,
    insights_snapshots: InsightsSnapshots,
    sessions: Sessions,
    after_name: str,
    before_name: str,
    session_name: str,
) -> None:
    assert_completed_session_delta(
        insights_snapshots.get(before_name),
        insights_snapshots.get(after_name),
        client.sessions.snapshot(sessions.get(session_name)),
    )


@then(parsers.parse(
    'resumable list "{list_name}" contains session "{session_name}"'
))
def resumable_list_contains_session(
    resumable_lists: ResumableLists,
    sessions: Sessions,
    list_name: str,
    session_name: str,
) -> None:
    _session_row(resumable_lists, sessions, list_name, session_name)


@then(parsers.parse(
    'resumable list "{list_name}" contains only session "{session_name}"'
))
def resumable_list_contains_only_session(
    resumable_lists: ResumableLists,
    sessions: Sessions,
    list_name: str,
    session_name: str,
) -> None:
    expected_session_id = sessions.get(session_name).session_id
    actual_session_ids = tuple(
        item.session_id for item in resumable_lists.get(list_name)
    )
    assert actual_session_ids == (expected_session_id,), (
        f"resumable list {list_name!r} has session IDs {actual_session_ids!r}"
    )


@then(parsers.parse(
    'resumable list "{list_name}" shows session "{session_name}" as {state}'
))
def resumable_list_shows_session_state(
    resumable_lists: ResumableLists,
    sessions: Sessions,
    list_name: str,
    session_name: str,
    state: str,
) -> None:
    if state not in {"active", "inactive"}:
        raise AssertionError(f"unknown resumable session state {state!r}")
    row = _session_row(resumable_lists, sessions, list_name, session_name)
    assert row.active is (state == "active")


@then(parsers.parse(
    'resumable list "{list_name}" orders session "{newer_session_name}" '
    'before session "{older_session_name}" by newest activity'
))
def resumable_list_orders_sessions_by_activity(
    resumable_lists: ResumableLists,
    sessions: Sessions,
    list_name: str,
    newer_session_name: str,
    older_session_name: str,
) -> None:
    rows = resumable_lists.get(list_name)
    activity = tuple(row.last_activity_at for row in rows)
    assert activity == tuple(sorted(activity, reverse=True)), (
        f"resumable list {list_name!r} is not in newest-first order"
    )
    newer = _session_row(
        resumable_lists,
        sessions,
        list_name,
        newer_session_name,
    )
    older = _session_row(
        resumable_lists,
        sessions,
        list_name,
        older_session_name,
    )
    assert newer.last_activity_at > older.last_activity_at
    assert rows.index(newer) < rows.index(older)
