"""Binds features/greeting.feature to pytest.

One module per feature file. The scenarios' own steps are in conftest.py; the
only thing that belongs here is the timeout, because a real model answering a
real prompt has nothing to do with the 30 s the hermetic suite runs under.
"""

from __future__ import annotations

import pytest
from pytest_bdd import scenarios

pytestmark = [pytest.mark.drift, pytest.mark.timeout(900)]

scenarios("features/greeting.feature")
