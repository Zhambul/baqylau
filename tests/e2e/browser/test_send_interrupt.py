"""Browser coverage for prompt confirmation at the interrupt boundary."""

from __future__ import annotations

import os

import pytest
from playwright.sync_api import Page, Request, expect
from pytest_bdd import parsers, scenarios, then

from tests.e2e.testkit.policy import WaitPolicy

pytestmark = [
    pytest.mark.browser,
    pytest.mark.drift,
    pytest.mark.timeout(900),
    pytest.mark.skipif(
        not os.environ.get("BAQYLAU_E2E_BROWSER"),
        reason="real-browser E2E tests are opt-in",
    ),
]

scenarios("../features/send_interrupt.feature")


@then(parsers.parse("the browser shows confirmed prompt '{text}'"))
def browser_shows_confirmed_prompt(
    page: Page,
    wait_policy: WaitPolicy,
    text: str,
) -> None:
    timeout = round(wait_policy.feed * 1_000)
    expect(
        page.locator(".msg.prompt.pending").filter(has_text=text)
    ).to_have_count(0, timeout=timeout)
    expect(
        page.locator(".msg.prompt:not(.pending):not(.queued)").filter(
            has_text=text
        )
    ).to_have_count(1, timeout=timeout)


@then("one idle Escape does not request Stop")
def idle_escape_does_not_request_stop(page: Page) -> None:
    interrupt_requests: list[str] = []

    def record(request: Request) -> None:
        if request.method == "POST" and request.url.endswith("/controls/interrupt"):
            interrupt_requests.append(request.url)

    page.on("request", record)
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(1_000)
    finally:
        page.remove_listener("request", record)
    assert interrupt_requests == [], (
        f"idle Escape requested Stop: {interrupt_requests}"
    )
