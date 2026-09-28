# Copyright (c) 2026 Zhambyl Yermagambet
"""A terminal view can name only a declared query that accepts every view scope (P10-T02)."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.manifest.validation import validate_manifest
from baqylau_extension_api.manifest.views import TerminalView
from baqylau_extension_api.terminal.models import TerminalSelection, TerminalViewInput

from tests.extension_api import operation_samples

VIEW_ID = f"{operation_samples.OWNER}.view"
INSTALLATION = ("installation",)


def with_view(query: str | None, scopes: tuple[str, ...] = INSTALLATION) -> ExtensionManifest:
    """Add one terminal view that names a query to the sample operations package.

    Returns:
        The manifest, before validation.

    """
    manifest = operation_samples.manifest()
    view = TerminalView.model_validate({
        "view_id": VIEW_ID, "title": "View", "scopes": scopes, "pane": "view", "query": query,
    })
    contributions = manifest.contributions.model_copy(update={"terminal": (view,)})
    return manifest.model_copy(update={
        "capabilities": (*manifest.capabilities, "terminal"), "contributions": contributions,
    })


def test_view_names_a_declared_query() -> None:
    """A declared query in the view's scope is accepted, and so is a view without a query."""
    assert validate_manifest(with_view(operation_samples.QUERY_ID)).contributions.terminal[0].query
    assert validate_manifest(with_view(None)).contributions.terminal[0].query is None


@pytest.mark.parametrize(("query", "scopes"), [
    (f"{operation_samples.OWNER}.missing", INSTALLATION),
    (operation_samples.COMMAND_ID, INSTALLATION),
    (operation_samples.QUERY_ID, (*INSTALLATION, "session")),
])
def test_view_query_is_refused(query: str, scopes: tuple[str, ...]) -> None:
    """An unknown query, a command, or a query that does not accept every view scope is refused."""
    with pytest.raises(ExtensionContractError, match="terminal view query"):
        validate_manifest(with_view(query, scopes))


def test_view_input_carries_the_focus() -> None:
    """The host's query arguments are the pane's focus, or no focus."""
    focus = TerminalSelection(block_id="log", item_id="abc")

    assert TerminalViewInput(selection=focus).model_dump_json() == '{"selection":{"block_id":"log","item_id":"abc"}}'
    assert TerminalViewInput().model_dump_json() == '{"selection":null}'
