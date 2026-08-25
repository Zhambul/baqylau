"""Real browser journeys against the live harness application."""

from __future__ import annotations

import os

import pytest
from pytest_bdd import scenarios

pytestmark = [
    pytest.mark.browser,
    pytest.mark.drift,
    pytest.mark.timeout(900),
    pytest.mark.skipif(
        not os.environ.get("BAQYLAU_E2E_BROWSER"),
        reason="real-browser E2E tests are opt-in",
    ),
]

scenarios("../features/browser.feature")
