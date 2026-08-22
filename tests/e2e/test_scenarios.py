"""Every live-harness scenario, bound to pytest.

One module for all five features. It used to be one module per feature, five
files whose entire content was the same two lines with a different filename —
which is a directory pretending to be a structure. Selection is by name now:

    make test-drift E2E="-k greeting"
    make test-drift E2E="-k 'monitor or background'"

The markers are here because they are the same for every scenario and belong to
the SUITE rather than to any one of them: `drift` is what this suite is (it
catches a harness drifting under us), and the timeout is generous because a real
model answering a real prompt has nothing to do with the 30 s the hermetic suite
runs under.
"""

from __future__ import annotations

import pytest
from pytest_bdd import scenarios

pytestmark = [pytest.mark.drift, pytest.mark.timeout(900)]

scenarios(
    "features/background.feature",
    "features/greeting.feature",
    "features/monitor.feature",
    "features/shell.feature",
    "features/subagent.feature",
    "features/usage.feature",
)
