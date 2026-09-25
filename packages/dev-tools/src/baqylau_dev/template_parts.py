# Copyright (c) 2026 Zhambyl Yermagambet
"""Give the text parts of the extension template: the terminal and web code and their checks."""

from dataclasses import dataclass

WEB_MODULE = "web/view.js"
TERMINAL_CLASS = '''

class Status(presentation.ExtensionTerminalPresenter):
    """Draw one status line in the terminal view."""

    def present(self, terminal_request: TerminalViewRequest) -> TerminalView:
        """Draw the view.

        Returns:
            The view, bound to the request.

        """
        status = StatusBlock(block_id="status", label="Ready", tone="success")
        return TerminalView(binding=terminal_request.binding, title="Status", blocks=(status,))
'''
TERMINAL_KEYS = ("terminal_summary", "terminal_contract", "terminal_imports", "terminal_class", "terminal_capability")
TERMINAL_IMPORTS = (
    "from baqylau_extension_api.terminal.blocks import StatusBlock\n"
    "from baqylau_extension_api.terminal.models import TerminalView, TerminalViewRequest\n"
)
TERMINAL_CHECK = '''

def check_terminal(client: HostClient) -> None:
    """Read the terminal view through the host's terminal route."""
    view = PaneRequest(extension_id=OWNER, view_id=f"{OWNER}.status", scope=INSTALLATION, window_id="none")
    assert terminal_view(client, view).title == "Status"
'''
WEB_CHECK = '''

def check_page(client: HostClient) -> None:
    """Open the workspace page in a headless browser and find the mounted view."""
    host_url = str(client.transport.base_url).rstrip("/")
    with sync_playwright() as playwright:
        page = playwright.chromium.launch().new_page()
        page.goto(workspace_view_url(host_url, "workspace-one", OWNER, f"{OWNER}.page"))
        expect(extension_view(page, f"{OWNER}.page")).to_contain_text(f"Hello from {OWNER}")
'''


@dataclass(frozen=True)
class CheckPart:
    """Keep one optional view's check in the E2E case: its imports, function, and call."""

    summary: str
    imports: tuple[str, ...]
    function: str
    call: str


BASE_IMPORTS = (
    "from baqylau_extension_api.models.queries import QueryReady\n",
    "from baqylau_extension_testkit.client import HostClient\n",
    "from baqylau_extension_testkit.data_models import QueryRequest\n",
    "from baqylau_extension_testkit.data_reads import query\n",
    "from baqylau_extension_testkit.lifecycle import change\n",
    "from baqylau_extension_testkit.pytest_plugin import PrivateHost\n",
    "from baqylau_extension_testkit.wheelhouse import build_environment\n",
)
TERMINAL_PART = CheckPart(
    "terminal view", ("from baqylau_extension_testkit.terminal_panes import PaneRequest, terminal_view\n",),
    TERMINAL_CHECK, "check_terminal",
)
WEB_PART = CheckPart(
    "web view",
    (
        "from baqylau_extension_testkit.browser import extension_view, workspace_view_url\n",
        "from playwright.sync_api import expect, sync_playwright\n",
    ),
    WEB_CHECK, "check_page",
)


def terminal_values(*, terminal: bool) -> dict[str, str]:
    """Fill the backend's terminal parts.

    Returns:
        The template values; empty parts for a package with no terminal view.

    """
    if not terminal:
        return dict.fromkeys(TERMINAL_KEYS, "")
    return {
        "terminal_summary": ", and its terminal view",
        "terminal_contract": ", presentation",
        "terminal_imports": TERMINAL_IMPORTS,
        "terminal_class": TERMINAL_CLASS,
        "terminal_capability": ", terminal=Status()",
    }


def e2e_values(*, web: bool, terminal: bool) -> dict[str, str]:
    """Fill the E2E case's checks of the declared views.

    Returns:
        The template values.

    """
    chosen = ((TERMINAL_PART, terminal), (WEB_PART, web))
    parts = [part for part, present in chosen if present]
    imports = [line for part in parts for line in part.imports]
    return {
        "e2e_summary": "".join(f", {part.summary}" for part in parts),
        "e2e_imports": "".join(sorted((*BASE_IMPORTS, *imports))),
        "e2e_functions": "".join(part.function for part in parts),
        "e2e_calls": "".join(f"    {part.call}(client)\n" for part in parts),
    }


WEB_TARGETS = """
# The web module's checks use the installed @baqylau/dev-tools rules; run npm install first.
lint-web:
\tnpm run check
\tnpm run lint
\tnpm run format:check
"""


def web_values(*, web: bool) -> dict[str, str]:
    """Fill the Makefile's web targets.

    Returns:
        The substitutions.

    """
    if not web:
        return {"web_phony": "", "web_lint": "", "web_targets": ""}
    return {"web_phony": " lint-web", "web_lint": " lint-web", "web_targets": WEB_TARGETS}
