# Copyright (c) 2026 Zhambyl Yermagambet
"""The extension benchmark runs end to end on a small capture, so the release measurement stays usable (P08-T04)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.perf import benchmark_capture, benchmark_idle, benchmark_probes, benchmark_profiles

if TYPE_CHECKING:
    from pathlib import Path

SMOKE_EVENTS = 5
TEST_TIMEOUT_SECONDS = 180


@pytest.mark.timeout(TEST_TIMEOUT_SECONDS)
def test_benchmark_measures_one_extension(runtime_wheels: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A no-op extension profile drains the capture and reports every measurement."""
    monkeypatch.setattr(benchmark_idle, "IDLE_SECONDS", 1.0)
    monkeypatch.setattr(benchmark_probes, "DELAY_SAMPLES", 3)
    hooks = benchmark_capture.capture(SMOKE_EVENTS)

    result = benchmark_profiles.measure("one", hooks, runtime_wheels)

    assert result.events == SMOKE_EVENTS + 1
    assert result.throughput_per_second > 0
    assert result.delay_median_ms > 0
    assert result.memory_mb > 0
