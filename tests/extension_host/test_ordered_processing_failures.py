# Copyright (c) 2026 Zhambyl Yermagambet
"""Retain earlier accepted decisions after a later worker result fails."""

from pathlib import Path

from extensions.models import interpretation_steps as steps
from tests.extension_host import (
    ordered_processing_fixture as fixture,
    process_fixture,
    shutdown_process_fixture as shutdown,
    source_daemon_fixture as source,
)

RAW_OPERATIONS = 2
CANONICAL_OPERATIONS = 4
APPLIED = "applied"
FAILED = "failed"


def test_bad_raw_reply_preserves_entire_input(tmp_path: Path, runtime_wheels: Path) -> None:
    """One invalid source document rejects the second worker's valid replacement too."""
    case = fixture.installed(tmp_path, runtime_wheels)
    with process_fixture.running_catalog(tmp_path) as client:
        fixture.enable(client)
        case.append('"bad-raw"\n')
        source.require_facts(case, (
            '"bad-raw/raw-first/canonical-first/canonical-second"', '"added/canonical-second"',
            '"bad-raw/canonical-first/canonical-second"',
        ))
        step = source.journals(case)[0].proposal.steps[1]
        assert isinstance(step, steps.RawTransformStep)
        assert isinstance(step.outcome, steps.FailedStep) and step.outcome.reply is not None
        assert len(step.outcome.reply.operations) == RAW_OPERATIONS
        assert case.texts() == ('"bad-raw"\n',)


def test_bad_fact_reply_preserves_earlier_worker(tmp_path: Path, runtime_wheels: Path) -> None:
    """A missing cause rejects the whole later reply while retaining the first addition."""
    case = fixture.installed(tmp_path, runtime_wheels)
    with process_fixture.running_catalog(tmp_path) as client:
        fixture.enable(client)
        case.append('"bad-fact"\n')
        source.require_facts(case, (
            '"bad-fact/raw-first/raw-second/canonical-first"', '"added"',
            '"bad-fact/raw-second/canonical-first"',
        ))
        trace = source.journals(case)[0].proposal.steps
        step = trace[-1]
        assert isinstance(step, steps.CanonicalTransformStep)
        assert isinstance(step.outcome, steps.FailedStep) and step.outcome.reply is not None
        assert len(step.outcome.reply.operations) == CANONICAL_OPERATIONS
        assert fixture.outcomes(source.journals(case)[0]) == (
            APPLIED, APPLIED, APPLIED, APPLIED, FAILED,
        )


def test_worker_exit_keeps_later_processing(tmp_path: Path, runtime_wheels: Path) -> None:
    """A process exit in canonical work preserves its earlier raw reply and later processing."""
    case = fixture.installed(tmp_path, runtime_wheels)
    with process_fixture.running_catalog(tmp_path) as client:
        fixture.enable(client)
        owned = shutdown.worker_processes(tmp_path)
        case.append('"crash"\n"next"\n')
        source.require_facts(case, (
            '"crash/raw-first/raw-second/canonical-second"', '"crash/raw-second/canonical-second"',
            '"next/raw-second/canonical-second"',
        ))
        completed = source.journals(case)
        shutdown.require_crashed_worker(completed[0].proposal.steps[0])
        assert fixture.outcomes(completed[0]) == (
            APPLIED, APPLIED, APPLIED, FAILED, APPLIED,
        )
        assert fixture.outcomes(completed[1]) == (
            FAILED, APPLIED, APPLIED, FAILED, APPLIED,
        )
        source.require_core_progress(case, 3)
    shutdown.require_closed(tmp_path, owned)
