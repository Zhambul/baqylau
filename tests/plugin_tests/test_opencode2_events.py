# Copyright (c) 2026 Zhambyl Yermagambet
"""Check OpenCode2 saved events at restart boundaries."""

from pathlib import Path

import pytest

from domain import event_actor, event_resource, event_session, outcomes, work_state
from domain.content import TextContent
from domain.ids import ActorId, SessionId
from harness.impl.opencode2 import actors, conversation, files, web
from harness.impl.opencode2.records import NativeRecord
from harness.impl.opencode2.sources import OpenCodeSource
from harness.impl.opencode2.translator import OpenCodeTranslator
from harness.models.raw_events import RawEventSourceContext

FIXTURE_DIRECTORY = Path(__file__).parents[1] / "e2e/fixtures"


@pytest.mark.parametrize("filename", [
    "audit_opencode2_greeting.jsonl", "audit_opencode2_child_question.jsonl", "audit_opencode2_background.jsonl",
    "audit_opencode2_files.jsonl",
    "audit_opencode2_patch.jsonl",
    "audit_opencode2_context.jsonl",
    "audit_opencode2_interrupt.jsonl",
    "audit_opencode2_web.jsonl",
    "audit_opencode2_reasoning.jsonl",
    "audit_opencode2_skills.jsonl",
    "audit_opencode2_child_failure.jsonl",
])
def test_opencode2_source_resumes_after_restart(filename: str) -> None:
    """Do not read a completed record twice after a reader restart."""
    context = RawEventSourceContext(
        session_id=SessionId("session-one"),
        lead_actor_id=ActorId("session-one:lead"),
        actor_id=ActorId("session-one:lead"),
        parent_actor_id=None,
        source_reference=str(FIXTURE_DIRECTORY / filename),
    )
    observations = OpenCodeSource(context).read(None)
    assert observations
    assert OpenCodeSource(context).watch_paths() == (str(FIXTURE_DIRECTORY / filename),)
    assert OpenCodeSource(context).read(observations[-1].source_position) == ()
    for observation in observations:
        first = OpenCodeTranslator().translate(observation)
        repeated = OpenCodeTranslator().translate(observation)
        assert first == repeated


def test_child_title_does_not_rename_root() -> None:
    """Keep native child title changes on their actor."""
    source = (FIXTURE_DIRECTORY / "audit_opencode2_context.jsonl").read_text()
    line = next(saved for saved in source.splitlines() if '"session.renamed"' in saved)
    record = NativeRecord.model_validate_json(line)
    child = record.model_copy(update={
        "session": record.session.model_copy(update={"parent_id": SessionId("parent-one")}),
    })
    assert record.event.details.title is not None
    assert conversation.payloads(record) == (
        event_session.SessionTitleChanged(record.event.details.title, work_state.TitleOrigin.SUMMARY),
    )
    assert conversation.payloads(child) == (event_actor.ActorNameChanged(record.event.details.title),)


def test_shell_notice_preserves_assignment() -> None:
    """A later shell notice must preserve the child's first assignment result."""
    path = FIXTURE_DIRECTORY / "audit_opencode2_child_shell_notice.jsonl"
    lines = path.read_text().splitlines()
    completed, notice = (NativeRecord.model_validate_json(line) for line in lines)
    finished = actors.payloads(completed)[0]
    assert isinstance(finished, event_actor.ActorAssignmentFinished)
    assert finished.result == TextContent("started")
    assert notice.message == "Background command finished with output `done`."
    assert actors.payloads(notice) == ()


def test_failed_child_finishes_assignment() -> None:
    """Keep the captured provider failure on the finished child assignment."""
    path = FIXTURE_DIRECTORY / "audit_opencode2_child_failure.jsonl"
    record = NativeRecord.model_validate_json(path.read_text())
    assignment, actor = actors.payloads(record)
    assert isinstance(assignment, event_actor.ActorAssignmentFinished)
    assert (assignment.outcome, assignment.result) == (outcomes.Outcome.FAILED, None)
    assert record.event.details.error is not None
    assert assignment.reason == record.event.details.error.message
    assert actor == event_actor.ActorFinished(assignment.reason)


def test_saved_web_results_keep_native_outcomes() -> None:
    """Do not turn a cancelled native search into a successful result."""
    lines = (FIXTURE_DIRECTORY / "audit_opencode2_web.jsonl").read_text().splitlines()
    fetched, failed = (NativeRecord.model_validate_json(line) for line in lines)
    fetched_payload = web.payloads(fetched)[0]
    failed_payload = web.payloads(failed)[0]
    assert isinstance(fetched_payload, event_resource.WebFetched)
    assert isinstance(failed_payload, event_resource.SearchPerformed)
    assert fetched_payload.outcome == outcomes.Outcome.SUCCEEDED
    assert failed_payload.outcome == outcomes.Outcome.FAILED


def test_saved_patch_results_name_written_files() -> None:
    """Read one native patch by the files it wrote.

    The patch tool has no path of its own. It names each file in its result, and
    a move names only the file it wrote, so the source of a move comes from the
    diff.
    """
    lines = (FIXTURE_DIRECTORY / "audit_opencode2_patch.jsonl").read_text().splitlines()
    payloads = [files.payloads(NativeRecord.model_validate_json(line))[0] for line in lines]
    written = [payload for payload in payloads if isinstance(payload, event_resource.FileAccessed)]
    assert [(payload.action, payload.path) for payload in written] == [
        (outcomes.FileAction.CREATED, "/projects/opencode-patch/alpha.txt"),
        (outcomes.FileAction.DELETED, "/projects/opencode-patch/alpha.txt"),
        (outcomes.FileAction.CREATED, "/projects/opencode-patch/beta.txt"),
        (outcomes.FileAction.RENAMED, "/projects/opencode-patch/gamma.txt"),
    ]
    assert [(payload.lines_added, payload.lines_removed) for payload in written] == [
        (1, 0), (0, 1), (1, 0), (0, 0),
    ]
    assert written[-1].previous_path == "/projects/opencode-patch/beta.txt"


def test_saved_edit_results_are_not_a_move() -> None:
    """Keep an edit an update.

    The edit tool names its file in the diff header WITHOUT the directory. A
    header that is read as an absolute path turns every edit into a move.
    """
    lines = (FIXTURE_DIRECTORY / "audit_opencode2_files.jsonl").read_text().splitlines()
    payloads = [files.payloads(NativeRecord.model_validate_json(line))[0] for line in lines]
    edited = [
        payload for payload in payloads
        if isinstance(payload, event_resource.FileAccessed) and payload.unified_diff is not None
    ]
    assert edited
    assert {payload.action for payload in edited} == {outcomes.FileAction.UPDATED}
    assert {payload.previous_path for payload in edited} == {None}
