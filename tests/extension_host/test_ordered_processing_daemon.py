# Copyright (c) 2026 Zhambyl Yermagambet
"""Check ordered raw and canonical processing through actual independent workers."""

from pathlib import Path

import pytest

from domain.records import RecordedTranslationDecision
from extensions.models import interpretation_steps as steps
from tests.extension_host import ordered_processing_fixture as fixture, process_fixture, source_daemon_fixture as source

# Two daemon starts with two workers take about 20 seconds without load; parallel runs need more.
TEST_TIMEOUT_SECONDS = 90


@pytest.mark.timeout(TEST_TIMEOUT_SECONDS)
@pytest.mark.parametrize("owners", [fixture.OWNERS, tuple(reversed(fixture.OWNERS))])
def test_workers_follow_declared_order(tmp_path: Path, runtime_wheels: Path, owners: tuple[str, ...]) -> None:
    """Activation order cannot change the processing of earlier generated data."""
    case = fixture.installed(tmp_path, runtime_wheels)
    with process_fixture.running_catalog(tmp_path) as client:
        fixture.enable(client, owners)
        case.append('"seed"\n')
        source.require_facts(case, fixture.expected("seed"))
        completed = source.journals(case)
        trace = completed[0].proposal.steps
        assert tuple(step.stage for step in trace) == (
            "raw", "raw", "extension_translation", "canonical", "canonical",
        )
        assert tuple(step.request.context.extension_id for step in trace if isinstance(
            step, (steps.RawTransformStep, steps.CanonicalTransformStep),
        )) == (*fixture.OWNERS, *fixture.OWNERS)
        assert fixture.outcomes(completed[0]) == ("applied",) * len(trace)
        assert case.texts() == ('"seed"\n',)
    with process_fixture.running_catalog(tmp_path):
        assert source.journals(case) == completed


@pytest.mark.parametrize("owner", fixture.OWNERS)
def test_each_worker_alone(tmp_path: Path, runtime_wheels: Path, owner: str) -> None:
    """One transform owner alone produces its complete output without its peer."""
    case = fixture.installed(tmp_path, runtime_wheels)
    with process_fixture.running_catalog(tmp_path) as client:
        fixture.enable(client, (owner,))
        case.append('"seed"\n')
        source.require_facts(case, fixture.expected_alone(owner, "seed"))
        trace = source.journals(case)[0].proposal.steps
        assert tuple(step.stage for step in trace) == ("raw", "extension_translation", "canonical")
        assert tuple(step.request.context.extension_id for step in trace if isinstance(
            step, (steps.RawTransformStep, steps.CanonicalTransformStep),
        )) == (owner, owner)


@pytest.mark.parametrize(("seed", "step_count"), [("raw-drop", 1), ("canonical-drop", 4)])
def test_complete_drop_keeps_next_original(
    tmp_path: Path, runtime_wheels: Path, seed: str, step_count: int,
) -> None:
    """An empty stage skips later empty calls and still completes the next original."""
    case = fixture.installed(tmp_path, runtime_wheels)
    with process_fixture.running_catalog(tmp_path) as client:
        fixture.enable(client)
        case.append(f'"{seed}"\n"next"\n')
        source.require_facts(case, fixture.expected("next"))
        completed = source.journals(case)
        assert tuple(commit.proposal.decision for commit in completed) == (
            RecordedTranslationDecision.SUPPRESSED, RecordedTranslationDecision.TRANSLATED,
        )
        assert len(completed[0].proposal.steps) == step_count
        assert completed[0].proposal.facts == ()
        source.require_core_progress(case, len(fixture.expected("next")))
