# Copyright (c) 2026 Zhambyl Yermagambet
"""Add a feed card for each finished turn, answer one query, and draw one terminal view.

The package imports only the public SDK and the standard library. The host runs
this module in a private worker process. Each capability is one small class.
"""

import hashlib
from dataclasses import dataclass

from baqylau_extension_api.contracts import lifecycle, operations, plugin, presentation, projection, services
from baqylau_extension_api.models import lifecycle as lifecycle_models, projections, queries
from baqylau_extension_api.models.canonical import CommittedFact
from baqylau_extension_api.models.documents import EncodedDocument, SchemaRef
from baqylau_extension_api.models.operations import QuerySnapshot
from baqylau_extension_api.models.projection_entries import ProjectedEntry
from baqylau_extension_api.terminal.blocks import StatusBlock
from baqylau_extension_api.terminal.models import TerminalView, TerminalViewRequest

CARD_TEXT = '"A turn finished."'
CARD_SUMMARY = "A turn finished."
GREETING = '"Hello from the example extension."'
# The manifest declares this schema with the same bytes; the digest names them.
TEXT_SCHEMA = '{"type":"string"}'
TEXT_DIGEST = hashlib.sha256(TEXT_SCHEMA.encode()).hexdigest()


@dataclass(frozen=True)
class Cards(projection.ExtensionProjector):
    """Draw one feed card for each finished turn, with a key that is stable for the fact."""

    owner: str

    def select_records(
        self, selection_request: projections.ProjectionSelectionRequest,
    ) -> projections.ProjectionReadSet:
        """Read no records.

        Returns:
            An empty read set.

        """
        return projections.ProjectionReadSet(binding=selection_request.binding)

    def project(self, projection_request: projections.ProjectionRequest) -> projections.ProjectionResult:
        """Draw the cards of the selected facts.

        Returns:
            The cards.

        """
        cards = tuple(self._card(committed) for committed in projection_request.events)
        return projections.ProjectionResult(binding=projection_request.binding, entries=cards)

    def _card(self, committed: CommittedFact) -> ProjectedEntry:
        schema = SchemaRef(owner=self.owner, name="text", version=1, digest=TEXT_DIGEST)
        event_id = committed.fact.event_id
        return ProjectedEntry(
            entry_key=f"card-{event_id}", source_event_id=event_id, entry_type=f"{self.owner}.card",
            document=EncodedDocument(schema_ref=schema, json_text=CARD_TEXT), summary=CARD_SUMMARY,
            occurred_at=committed.fact.occurred_at,
        )


class Greeting(operations.ExtensionQueries):
    """Answer the greeting query."""

    def query(self, query_request: queries.QueryRequest) -> queries.QueryResult:
        """Answer with the greeting in the package's text schema.

        Returns:
            The ready result.

        """
        document = query_request.arguments.model_copy(update={"json_text": GREETING})
        return queries.QueryReady(
            binding=query_request.binding, document=document, snapshot=QuerySnapshot(state_revision="static"),
        )


class Status(presentation.ExtensionTerminalPresenter):
    """Draw one status line in the terminal view."""

    def present(self, terminal_request: TerminalViewRequest) -> TerminalView:
        """Draw the view.

        Returns:
            The view, bound to the request.

        """
        status = StatusBlock(block_id="status", label="Hello", tone="success")
        return TerminalView(binding=terminal_request.binding, title="Hello", blocks=(status,))


@dataclass(frozen=True)
class HelloExtension(plugin.ExtensionPlugin, lifecycle.ExtensionLifecycle):
    """Give the host the declared capabilities, and no others."""

    host: services.ExtensionHostServices

    @property
    def extension_info(self) -> lifecycle_models.ExtensionInfo:
        """The package identity that the host selected."""
        return self.host.environment.extension_info

    @property
    def capabilities(self) -> plugin.ExtensionCapabilities:
        """The lifecycle, projector, queries, and terminal view."""
        cards = Cards(self.extension_info.extension_id)
        return plugin.ExtensionCapabilities(lifecycle=self, projector=cards, queries=Greeting(), terminal=Status())

    def activate(self, request: lifecycle_models.ActivationRequest) -> lifecycle_models.ActivationResult:
        """Accept the runtime; this package starts nothing.

        Returns:
            Readiness.

        """
        return lifecycle_models.ActivationReady(runtime_revision=request.runtime_revision)

    def deactivate(self, request: lifecycle_models.DeactivationRequest) -> lifecycle_models.DeactivationResult:
        """Stop; nothing runs.

        Returns:
            Completion.

        """
        return lifecycle_models.DeactivationResult(runtime_revision=request.runtime_revision)


def build_extension(host: services.ExtensionHostServices) -> plugin.ExtensionPlugin:
    """Construct the feature in its worker.

    Returns:
        The plugin.

    """
    return HelloExtension(host)
