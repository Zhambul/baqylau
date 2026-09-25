# Copyright (c) 2026 Zhambyl Yermagambet
"""Add one feed card for each finished turn of a session (C14 fixture)."""

import hashlib

from baqylau_extension_api.contracts import lifecycle, plugin, projection, services
from baqylau_extension_api.models import canonical, lifecycle as lifecycle_models, projections
from baqylau_extension_api.models.documents import EncodedDocument, SchemaRef
from baqylau_extension_api.models.projection_entries import ProjectedEntry

TEXT_SCHEMA = '{"type":"string"}'
TEXT_DIGEST = hashlib.sha256(TEXT_SCHEMA.encode()).hexdigest()
SUMMARY = "Turn card from the extension."
# A test copy of this module can change the count to draw a large entry list from one turn.
CARDS_PER_TURN = 1


def card(owner: str, committed: canonical.CommittedFact, index: int) -> ProjectedEntry:
    """Draw one card of one finished turn.

    Returns:
        The feed entry.

    """
    reference = SchemaRef(owner=owner, name="text", version=1, digest=TEXT_DIGEST)
    fact = committed.fact
    return ProjectedEntry(
        entry_key=f"card-{fact.event_id}-{index}", source_event_id=fact.event_id, entry_type=f"{owner}.card",
        document=EncodedDocument(schema_ref=reference, json_text='"finished"'), summary=f"{SUMMARY} #{index}",
        occurred_at=fact.occurred_at,
    )


class SessionCards(plugin.ExtensionPlugin, lifecycle.ExtensionLifecycle, projection.ExtensionProjector):
    """Project finished turns into feed cards; keep no records."""

    def __init__(self, host_services: services.ExtensionHostServices) -> None:
        """Keep the host services."""
        self._services = host_services

    @property
    def extension_info(self) -> lifecycle_models.ExtensionInfo:
        """The selected package identity."""
        return self._services.environment.extension_info

    @property
    def capabilities(self) -> plugin.ExtensionCapabilities:
        """The lifecycle and the projector."""
        return plugin.ExtensionCapabilities(lifecycle=self, projector=self)

    def activate(self, request: lifecycle_models.ActivationRequest) -> lifecycle_models.ActivationResult:
        """Activate.

        Returns:
            Readiness.

        """
        return lifecycle_models.ActivationReady(runtime_revision=request.runtime_revision)

    def deactivate(self, request: lifecycle_models.DeactivationRequest) -> lifecycle_models.DeactivationResult:
        """Stop.

        Returns:
            Completion.

        """
        return lifecycle_models.DeactivationResult(runtime_revision=request.runtime_revision)

    def select_records(
        self, selection_request: projections.ProjectionSelectionRequest,
    ) -> projections.ProjectionReadSet:
        """Read no records.

        Returns:
            An empty read set.

        """
        return projections.ProjectionReadSet(binding=selection_request.binding)

    def project(self, projection_request: projections.ProjectionRequest) -> projections.ProjectionResult:
        """Draw one card for each selected fact.

        Returns:
            The cards.

        """
        owner = self.extension_info.extension_id
        entries = tuple(
            card(owner, committed, index) for committed in projection_request.events for index in range(CARDS_PER_TURN)
        )
        return projections.ProjectionResult(binding=projection_request.binding, entries=entries)


def build_extension(host_services: services.ExtensionHostServices) -> plugin.ExtensionPlugin:
    """Construct the feature in its worker.

    Returns:
        The plugin.

    """
    return SessionCards(host_services)
