# Copyright (c) 2026 Zhambyl Yermagambet
"""Read the document of a terminal view that names one of its package's queries.

A presenter is pure, so it cannot read live data itself. The host runs the
view's declared query with the pane's focus, and the presenter gets the ready
result as its document. A failed query is a failed view: the pane shows the
failure and tries again after the next change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from baqylau_extension_api.terminal.models import TerminalViewInput

from extensions import query_calls
from extensions.terminal_views import TerminalViewFailedError

if TYPE_CHECKING:
    from baqylau_extension_api.manifest.views import TerminalView
    from baqylau_extension_api.models.documents import EncodedDocument
    from baqylau_extension_api.models.scopes import ExtensionScope
    from baqylau_extension_api.terminal.models import TerminalSelection

    from extensions.query_authority import QueryAuthority
    from extensions.registry_package import RegistryPackage


def view_document(
    package: RegistryPackage, view: TerminalView, scope: ExtensionScope, focus: TerminalSelection | None,
    authority: QueryAuthority,
) -> EncodedDocument | None:
    """Run the view's query in the view's scope with the pane's focus.

    Returns:
        The query's result document, or None for a view without a query.

    Raises:
        TerminalViewFailedError: If the package has no queries, or the query fails or gives a result that is not valid.

    """
    if view.query is None:
        return None
    capability = None if package.plugin is None else package.plugin.capabilities.queries
    if capability is None:
        message = "the extension view has no active query"
        raise TerminalViewFailedError(message)
    binding = query_calls.query_binding(package, view.query, scope)
    request = query_calls.query_request(package, binding, TerminalViewInput(selection=focus).model_dump_json())
    try:
        return authority.run(package, scope, lambda: query_calls.ready_query(package, capability, request)).document
    except Exception as error:
        # Any worker failure or refused result is one host message; worker text never reaches the pane.
        message = "the extension view query failed"
        raise TerminalViewFailedError(message) from error
