# Copyright (c) 2026 Zhambyl Yermagambet
"""The host selects the consecutive-failure limit through its environment (P03-T05)."""

import pytest

from extensions.configuration import FAILURE_LIMIT_ENVIRONMENT, configured_health_policy
from extensions.models.extension_health import DEFAULT_FAILURE_LIMIT

SELECTED_LIMIT = 2


def test_limit_comes_from_the_environment() -> None:
    """An absent value is the default; a positive integer selects the limit."""
    assert configured_health_policy({}).failure_limit == DEFAULT_FAILURE_LIMIT
    selected = configured_health_policy({FAILURE_LIMIT_ENVIRONMENT: str(SELECTED_LIMIT)})
    assert selected.failure_limit == SELECTED_LIMIT


@pytest.mark.parametrize("configured", ["0", "-1", "many"])
def test_limit_must_be_positive(configured: str) -> None:
    """A limit that is not a positive integer stops startup with a clear message."""
    with pytest.raises(ValueError, match="positive integer"):
        configured_health_policy({FAILURE_LIMIT_ENVIRONMENT: configured})
