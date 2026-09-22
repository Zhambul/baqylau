# Copyright (c) 2026 Zhambyl Yermagambet
"""Supply actual bounded repository snapshots to the processing capability."""

from pathlib import Path

from tests.extension_host import observation_requests, processing_pipeline_fixture as fixture


def test_batch_captures_complete_empty_state(tmp_path: Path) -> None:
    """The host proves an empty scope before calling its first canonical transform."""
    case = fixture.installed(tmp_path)
    assert case.run_batch() == 1
    call = case.probes.canonical.transform.call_args
    request = call.args[0]
    assert request.prior_state.complete
    assert request.prior_state.after_cursor == 0 and not request.prior_state.facts
    case.probes.core.accept_interpretation.assert_called_once()


def test_next_original_receives_prior_facts(tmp_path: Path) -> None:
    """A later transform receives the first accepted body and its actual boundary."""
    case = fixture.installed(tmp_path)
    assert case.run_batch() == 1
    later = observation_requests.new_key(case.original.original.request, "later")
    case.original.original.store.append_observations(later)
    assert case.run_batch() == 1
    call = case.probes.canonical.transform.call_args
    request = call.args[0]
    assert request.prior_state.complete and request.prior_state.after_cursor == 1
    assert len(request.prior_state.facts) == 1
    assert request.prior_state.facts[0].fact == request.inputs[0]
