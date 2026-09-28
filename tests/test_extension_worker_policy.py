# Copyright (c) 2026 Zhambyl Yermagambet
"""A pure transform call has its own shorter deadline, and never more than the call deadline (P08-T04)."""

import pytest

from extensions.configuration import CALL_SECONDS_ENVIRONMENT, TRANSFORM_SECONDS_ENVIRONMENT, configured_worker_policy
from extensions.models.workers import WorkerPolicy

DEFAULT_CALL_SECONDS = 30.0
DEFAULT_TRANSFORM_SECONDS = 5.0
SHORT_SECONDS = 1.0


def test_defaults_keep_pure_calls_short() -> None:
    """Without settings, a call has 30 seconds and a pure transform call has 5."""
    policy = configured_worker_policy({})

    assert (policy.request_seconds, policy.pure_seconds) == (DEFAULT_CALL_SECONDS, DEFAULT_TRANSFORM_SECONDS)


def test_both_deadlines_come_from_the_environment() -> None:
    """Each deadline has its own variable; a lower call deadline also caps pure calls."""
    policy = configured_worker_policy({CALL_SECONDS_ENVIRONMENT: "2", TRANSFORM_SECONDS_ENVIRONMENT: "4"})

    assert (policy.request_seconds, policy.transform_seconds, policy.pure_seconds) == (2.0, 4.0, 2.0)
    assert WorkerPolicy(transform_seconds=SHORT_SECONDS).pure_seconds == SHORT_SECONDS


@pytest.mark.parametrize("configured", ["0", "-1", "soon"])
def test_transform_deadline_must_be_positive(configured: str) -> None:
    """A transform deadline that is not a positive number stops startup with a clear message."""
    with pytest.raises(ValueError, match=TRANSFORM_SECONDS_ENVIRONMENT):
        configured_worker_policy({TRANSFORM_SECONDS_ENVIRONMENT: configured})
