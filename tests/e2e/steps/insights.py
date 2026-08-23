"""Named application insight reads and insight checks."""

from __future__ import annotations

from pytest_bdd import parsers, then, when

from sdk.client import BaqylauClient
from tests.e2e.testkit.references import InsightsSnapshots, ResumableLists, Sessions


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
    'resumable list "{list_name}" contains session "{session_name}"'
))
def resumable_list_contains_session(
    resumable_lists: ResumableLists,
    sessions: Sessions,
    list_name: str,
    session_name: str,
) -> None:
    session_id = sessions.get(session_name).session_id
    found = [item for item in resumable_lists.get(list_name) if item.session_id == session_id]
    assert len(found) == 1, (
        f"resumable list {list_name!r} has {len(found)} rows for session {session_name!r}"
    )
