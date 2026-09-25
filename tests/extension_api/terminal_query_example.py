# Copyright (c) 2026 Zhambyl Yermagambet
"""Draw a terminal view from the result of the view's own query.

The query answers with the focused item of the pane, and the presenter shows
the document that the host gives it. Only public SDK imports are used.
"""

import hashlib
from dataclasses import dataclass

from baqylau_extension_api.contracts import lifecycle as lifecycle_contract, operations, plugin, presentation, services
from baqylau_extension_api.models import lifecycle, queries
from baqylau_extension_api.models.documents import EncodedDocument, SchemaRef
from baqylau_extension_api.models.operations import QuerySnapshot
from baqylau_extension_api.terminal.blocks import TextBlock
from baqylau_extension_api.terminal.models import TerminalView, TerminalViewInput, TerminalViewRequest
from baqylau_extension_api.terminal.text import TextLine, TextSpan

# The test manifest declares these schemas with the same bytes; the digest names them.
ARGUMENTS_SCHEMA = '{"type":"object"}'
RESULT_SCHEMA = '{"type":"string"}'
RESULT_NAME = "focus"
NO_FOCUS = "none"


class FocusQuery(operations.ExtensionQueries):
    """Answer with the item that the pane focuses."""

    def query(self, query_request: queries.QueryRequest) -> queries.QueryResult:
        """Read the focus from the host's view input.

        Returns:
            The focused item ID as a JSON string.

        """
        view_input = TerminalViewInput.model_validate_json(query_request.arguments.json_text)
        focus = NO_FOCUS if view_input.selection is None else view_input.selection.item_id or NO_FOCUS
        digest = hashlib.sha256(RESULT_SCHEMA.encode()).hexdigest()
        schema = SchemaRef(owner=query_request.binding.extension_id, name=RESULT_NAME, version=1, digest=digest)
        document = EncodedDocument(schema_ref=schema, json_text=f'"{focus}"')
        return queries.QueryReady(
            binding=query_request.binding, document=document, snapshot=QuerySnapshot(state_revision="static"),
        )


@dataclass(frozen=True)
class QueryView(plugin.ExtensionPlugin, lifecycle_contract.ExtensionLifecycle, presentation.ExtensionTerminalPresenter):
    """Give the host a lifecycle, the focus query, and the presenter."""

    identity: lifecycle.ExtensionInfo

    @property
    def extension_info(self) -> lifecycle.ExtensionInfo:
        """The package identity that the host selected."""
        return self.identity

    @property
    def capabilities(self) -> plugin.ExtensionCapabilities:
        """The lifecycle, queries, and terminal presenter."""
        return plugin.ExtensionCapabilities(lifecycle=self, queries=FocusQuery(), terminal=self)

    def activate(self, request: lifecycle.ActivationRequest) -> lifecycle.ActivationResult:
        """Accept the runtime.

        Returns:
            Readiness.

        """
        return lifecycle.ActivationReady(runtime_revision=request.runtime_revision)

    def deactivate(self, request: lifecycle.DeactivationRequest) -> lifecycle.DeactivationResult:
        """Stop; nothing runs.

        Returns:
            Completion.

        """
        return lifecycle.DeactivationResult(runtime_revision=request.runtime_revision)

    def present(self, terminal_request: TerminalViewRequest) -> TerminalView:
        """Show the view document that the host read with the view query.

        Returns:
            The view.

        """
        document = terminal_request.document
        text = "no document" if document is None else f"document {document.json_text}"
        content = TextLine(spans=(TextSpan(text=text),))
        block = TextBlock(block_id="document", content=content)
        return TerminalView(binding=terminal_request.binding, title="Query view", blocks=(block,))


def build_extension(host: services.ExtensionHostServices) -> plugin.ExtensionPlugin:
    """Build the plugin in its worker.

    Returns:
        The plugin.

    """
    return QueryView(host.environment.extension_info)
