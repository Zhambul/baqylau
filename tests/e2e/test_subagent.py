"""Binds features/subagent.feature to pytest. See test_greeting.py for the pattern."""

from __future__ import annotations

import pytest
from pytest_bdd import scenarios

pytestmark = [pytest.mark.drift, pytest.mark.timeout(900)]

scenarios("features/subagent.feature")
