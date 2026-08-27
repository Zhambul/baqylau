"""Real harness journeys through Kitty and the dashboard API."""

from __future__ import annotations

import os

import pytest
from pytest_bdd import scenarios

pytestmark = [
    pytest.mark.drift,
    pytest.mark.kitty,
    pytest.mark.timeout(900),
    pytest.mark.skipif(
        not os.environ.get("BAQYLAU_E2E_REAL_TERMINAL"),
        reason="real-terminal E2E tests are opt-in",
    ),
]

scenarios(
    "../features/background_restart.feature",
    "../features/journeys.feature",
    "../features/runtime_restart.feature",
    "../features/terminal.feature",
)
