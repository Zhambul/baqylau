# Copyright (c) 2026 Zhambyl Yermagambet
"""Read the terminal view that an extension pane paints, and open a pane in a live terminal.

`terminal_view` is repeatable: the host presents and checks the view at a
pane size, as it does for a real pane. `open_pane` needs a live terminal
window, so a case that uses it is a live case.
"""

from urllib.parse import quote, urlencode

from baqylau_extension_api.terminal.models import TerminalView
from pydantic import BaseModel, ConfigDict

from baqylau_extension_testkit.client import HostClient
from baqylau_extension_testkit.lifecycle_models import HostDocument


class TerminalViewReply(HostDocument):
    """Keep the checked view."""

    view: TerminalView


class PaneRequest(BaseModel):
    """Open one view beside a terminal window."""

    model_config = ConfigDict(frozen=True)

    extension_id: str
    view_id: str
    scope: str
    window_id: str

    def view_path(self, columns: int, rows: int) -> str:
        """Build the path of the view at one pane size.

        Returns:
            The request path.

        """
        size = (("columns", str(columns)), ("rows", str(rows)))
        selection = urlencode((("scope", self.scope), *size))
        extension_part = quote(self.extension_id, safe="")
        view_part = quote(self.view_id, safe="")
        return f"/api/extension-terminal/views/{extension_part}/{view_part}?{selection}"


class PaneReply(HostDocument):
    """Tell whether a pane opened or took focus, its window, and why not."""

    opened: bool
    focused: bool
    window_id: str | None = None
    reason: str | None = None


def terminal_view(client: HostClient, request: PaneRequest, columns: int = 80, rows: int = 24) -> TerminalView:
    """Present one view at a pane size, as the pane reads it; the window ID is not used.

    Returns:
        The checked view.

    """
    return client.read(request.view_path(columns, rows), TerminalViewReply).view


def open_pane(client: HostClient, request: PaneRequest) -> PaneReply:
    """Open the view beside a live terminal window, or focus the pane that shows it.

    Returns:
        The host's reply.

    """
    return client.send("/api/extension-terminal/panes", request, PaneReply)
