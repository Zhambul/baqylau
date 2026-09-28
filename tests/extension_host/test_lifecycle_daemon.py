# Copyright (c) 2026 Zhambyl Yermagambet
"""Accept extension activity changes through actual daemon and worker processes."""

from pathlib import Path

from domain.records import RecordedTranslationDecision
from tests.extension_host import (
    lifecycle_daemon_checks as checks,
    lifecycle_daemon_fixture as fixture,
    process_fixture,
    public_audit_checks as audit,
)

DROP_BEHAVIOR = "drop"
REPLACE_BEHAVIOR = "replace"
FAIL_BEHAVIOR = "fail"
INVALID_BEHAVIOR = "invalid"
REQUIRED_KINDS = ("session.started", "actor.started")
FINISHED_KINDS = (*REQUIRED_KINDS, "session.finished")
UNCHANGED_KINDS = (*REQUIRED_KINDS, "turn.finished")
REQUIRED_STAGES = ("core_lifecycle", "core_activity", "canonical")
FINISH_STAGES = ("core_lifecycle", "core_activity")
EXTRA_TEXT = '"lifecycle extra"'
ENABLE_ACTION: fixture.LifecycleAction = "enable"
STOP_HOOK = "Stop"
STOP_IDENTITY = "stop-one"
SECOND_SESSION = "session-two"


def test_dropped_activity_keeps_session(tmp_path: Path, runtime_wheels: Path) -> None:
    """A suppressed activity reply keeps the required session and its complete journal."""
    case = fixture.installed(tmp_path, runtime_wheels, DROP_BEHAVIOR)
    with process_fixture.running_catalog(tmp_path) as client:
        case.change(client, ENABLE_ACTION)
        fixture.deliver_hook(client, fixture.hook(STOP_HOOK, STOP_IDENTITY))
        fixture.require_core_facts(case, REQUIRED_KINDS)
        checks.require_extension_texts(case, ())
        checks.require_drained(case)

        commit = checks.only_journal(case)
        assert commit.proposal.decision == RecordedTranslationDecision.TRANSLATED
        assert checks.step_stages(commit) == REQUIRED_STAGES
        assert checks.operation_kinds(commit) == ("drop",)


def test_replaced_activity_records_fact(tmp_path: Path, runtime_wheels: Path) -> None:
    """A replaced and added reply keeps the required start and records its own fact."""
    case = fixture.installed(tmp_path, runtime_wheels, REPLACE_BEHAVIOR)
    with process_fixture.running_catalog(tmp_path) as client:
        case.change(client, ENABLE_ACTION)
        fixture.deliver_hook(client, fixture.hook(STOP_HOOK, STOP_IDENTITY))
        fixture.require_core_facts(case, (*REQUIRED_KINDS, "turn.finished"))
        checks.require_extension_texts(case, (EXTRA_TEXT,))
        checks.require_drained(case)

        commit = checks.only_journal(case)
        assert commit.proposal.decision == RecordedTranslationDecision.TRANSLATED
        assert checks.step_stages(commit) == REQUIRED_STAGES
        assert checks.operation_kinds(commit) == ("replace", "insert")


def test_dropped_finish_activity_releases(tmp_path: Path, runtime_wheels: Path) -> None:
    """A finish input keeps its required finish, releases, and lets later input process."""
    case = fixture.installed(tmp_path, runtime_wheels, DROP_BEHAVIOR)
    with process_fixture.running_catalog(tmp_path) as client:
        case.change(client, ENABLE_ACTION)
        fixture.deliver_hook(client, fixture.hook(STOP_HOOK, STOP_IDENTITY))
        fixture.deliver_hook(client, fixture.hook("SessionEnd", "finish-one"))
        fixture.require_core_facts(case, FINISHED_KINDS)
        fixture.deliver_hook(client, fixture.hook("SessionStart", "start-two", SECOND_SESSION))
        fixture.require_core_facts(case, (*FINISHED_KINDS, *REQUIRED_KINDS))
        checks.require_extension_texts(case, ())
        checks.require_drained(case)

        finish = case.journals()[1]
        assert checks.fact_kinds(finish) == ("session.finished",)
        assert checks.step_stages(finish) == FINISH_STAGES


def test_failed_worker_keeps_required_facts(tmp_path: Path, runtime_wheels: Path) -> None:
    """A failed worker keeps the required session, records it, and retries safely."""
    case = fixture.installed(tmp_path, runtime_wheels, FAIL_BEHAVIOR)
    with process_fixture.running_catalog(tmp_path) as client:
        case.change(client, ENABLE_ACTION)
        fixture.deliver_hook(client, fixture.hook(STOP_HOOK, STOP_IDENTITY))
        fixture.require_core_facts(case, UNCHANGED_KINDS)
        before = case.journals()
        fixture.deliver_hook(client, fixture.hook(STOP_HOOK, STOP_IDENTITY))
        checks.require_drained(case)

        assert case.journals() == before
        assert checks.fact_kinds(before[0]) == UNCHANGED_KINDS
        assert checks.failed_step(before[0]).reply is None


def test_invalid_reply_keeps_required_facts(tmp_path: Path, runtime_wheels: Path) -> None:
    """An invalid reply keeps the required session and retains its rejected reply."""
    case = fixture.installed(tmp_path, runtime_wheels, INVALID_BEHAVIOR)
    with process_fixture.running_catalog(tmp_path) as client:
        case.change(client, ENABLE_ACTION)
        fixture.deliver_hook(client, fixture.hook(STOP_HOOK, STOP_IDENTITY))
        fixture.require_core_facts(case, UNCHANGED_KINDS)
        checks.require_extension_texts(case, ())
        checks.require_drained(case)

        commit = checks.only_journal(case)
        assert checks.fact_kinds(commit) == UNCHANGED_KINDS
        assert checks.failed_step(commit).reply is not None
        failure = audit.public_step(client, case, "canonical")
        assert (failure.outcome, failure.diagnostic_code) == ("failed", "processing_call_failed")


def test_restart_keeps_stored_journal(tmp_path: Path, runtime_wheels: Path) -> None:
    """A restarted daemon keeps the accepted facts and does not repeat processing."""
    case = fixture.installed(tmp_path, runtime_wheels, DROP_BEHAVIOR)
    with process_fixture.running_catalog(tmp_path) as client:
        case.change(client, ENABLE_ACTION)
        fixture.deliver_hook(client, fixture.hook(STOP_HOOK, STOP_IDENTITY))
        fixture.require_core_facts(case, REQUIRED_KINDS)
        before = case.journals()
        assert len(before) == 1
    with process_fixture.running_catalog(tmp_path):
        assert case.journals() == before
        assert fixture.core_kinds(case) == REQUIRED_KINDS
        assert case.pending_count() == 0
