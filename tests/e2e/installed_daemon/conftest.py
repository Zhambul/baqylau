"""The installed-daemon suite must not start an isolated test daemon."""

from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def scenario_signoff() -> Iterator[None]:
    yield
