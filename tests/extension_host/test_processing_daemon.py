# Copyright (c) 2026 Zhambyl Yermagambet
"""Check complete source-to-fact processing in actual private daemon and worker processes."""

from pathlib import Path

from domain.records import RecordedTranslationDecision
from extensions.models import interpretation_steps as steps
from tests import terminal_pty_waits
from tests.extension_host import process_fixture, source_daemon_fixture as fixture

ORIGINAL_COUNT = 2


def test_daemon_converges_without_retranslation(tmp_path: Path, runtime_wheels: Path) -> None:
    """Two observations converge on one fact; restart keeps both complete interpretations."""
    case = fixture.installed(tmp_path, runtime_wheels, '"same"\n"same"\n')
    with process_fixture.running_catalog(tmp_path) as client:
        case.change(client, "enable")
        terminal_pty_waits.wait_until(lambda: len(fixture.journals(case)) == ORIGINAL_COUNT)
        fixture.require_facts(case, ('"same"\n',))
        before = fixture.journals(case)
        assert all(commit.proposal.decision == RecordedTranslationDecision.TRANSLATED for commit in before)
    with process_fixture.running_catalog(tmp_path):
        fixture.require_facts(case, ('"same"\n',))
        assert fixture.journals(case) == before


def test_failed_pure_call_keeps_next_input(tmp_path: Path, runtime_wheels: Path) -> None:
    """A forbidden live callback fails the decoder but does not hold the next original."""
    case = fixture.installed(tmp_path, runtime_wheels, '"host_call"\n"good"\n')
    with process_fixture.running_catalog(tmp_path) as client:
        case.change(client, "enable")
        fixture.require_facts(case, ('"good"\n',))
        completed = fixture.journals(case)
        assert tuple(commit.proposal.decision for commit in completed) == (
            RecordedTranslationDecision.TRANSLATION_FAILED, RecordedTranslationDecision.TRANSLATED,
        )
        step = completed[0].proposal.steps[0]
        assert isinstance(step, steps.ExtensionTranslationStep)
        assert isinstance(step.outcome, steps.FailedStep)
        assert len(case.originals()) == ORIGINAL_COUNT
