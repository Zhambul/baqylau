"""A real Chrome page for browser-origin Gherkin cases."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from playwright.sync_api import Page

from sdk.client import BaqylauClient
from tests.e2e.testkit.browser import BrowserSessionDriver
from tests.e2e.testkit.policy import WaitPolicy
from tests.e2e.testkit.process import ApplicationProcess


@pytest.fixture(scope="session")
def browser_type_launch_args(
    browser_type_launch_args: dict[str, object],
) -> dict[str, object]:
    return {**browser_type_launch_args, "channel": "chrome"}


@pytest.fixture
def browser_session_driver(
    page: Page,
    client: BaqylauClient,
    application_process: ApplicationProcess,
    workspace: str,
    wait_policy: WaitPolicy,
) -> Iterator[BrowserSessionDriver]:
    driver = BrowserSessionDriver(
        page,
        client,
        application_process.endpoint.url,
        workspace,
        wait_policy,
    )
    yield driver
    driver.assert_clean()
