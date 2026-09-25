# Copyright (c) 2026 Zhambyl Yermagambet
"""Run the example against a private host: its query, terminal view, and web view.

The feed card needs a real session, which only a live harness case can make; see the authoring guide.
"""

import shutil
from pathlib import Path

from baqylau_extension_testkit.browser import extension_view, workspace_view_url
from baqylau_extension_testkit.client import HostClient
from baqylau_extension_testkit.data_models import QueryRequest
from baqylau_extension_testkit.data_reads import query
from baqylau_extension_testkit.lifecycle import change
from baqylau_extension_testkit.pytest_plugin import PrivateHost
from baqylau_extension_testkit.signoff import signoff
from baqylau_extension_testkit.terminal_panes import PaneRequest, terminal_view
from playwright.sync_api import expect, sync_playwright
from pydantic import TypeAdapter

OWNER = "example.hello"
PAGE_VIEW = f"{OWNER}.page"
INSTALLATION = '{"kind":"installation"}'
PACKAGE = Path(__file__).resolve().parents[2]


def check_page(client: HostClient) -> None:
    """Open the workspace route in a headless browser and find the mounted view by its ID."""
    host_url = str(client.transport.base_url).rstrip("/")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(workspace_view_url(host_url, "workspace-one", OWNER, PAGE_VIEW))
        expect(extension_view(page, PAGE_VIEW)).to_contain_text(f"Hello from {OWNER}")
        browser.close()


def test_example(baqylau_host: PrivateHost) -> None:
    """Enable the package in a private host, then check each surface and sign off."""
    shutil.copytree(PACKAGE, baqylau_host.roots.packages / OWNER)
    client = baqylau_host.start()
    assert change(client, OWNER, "enable", "enable-example").status == "succeeded"

    greeting = query(client, OWNER, f"{OWNER}.greeting", QueryRequest(scope=INSTALLATION, arguments='"hi"'))
    assert TypeAdapter(str).validate_json(greeting.document.json_text) == "Hello from the example extension."
    view = PaneRequest(extension_id=OWNER, view_id=f"{OWNER}.status", scope=INSTALLATION, window_id="none")
    assert terminal_view(client, view).title == "Hello"
    check_page(client)
    assert signoff(client).raw_event_count == 0
