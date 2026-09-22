# Copyright (c) 2026 Zhambyl Yermagambet
"""Own feed and record behavior using only the installed extension SDK."""

from baqylau_extension_api.contracts import lifecycle, plugin, projection, services
from baqylau_extension_api.models import directory, events, lifecycle as lifecycle_models, projections, records
from baqylau_extension_api.models.projection_entries import ProjectedEntry
from baqylau_extension_api.models.record_changes import DeleteRecord, PutRecord, RecordChange


def selected_key(request: projections.ProjectionSelectionRequest) -> records.RecordKey:
    """Keep record-key rules in the feature package.

    Returns:
        The owned current-state key in the supplied scope.

    """
    context = request.binding.context
    return records.RecordKey(
        owner=context.extension_id, collection=f"{context.extension_id}.records", scope=context.scope, key="current",
    )


def feed_entries(fact: events.ExtensionFact) -> tuple[ProjectedEntry, ...]:
    """Draw a summary and detail row without changing the stored fact.

    Returns:
        Two ordered rows or no rows for a suppressed fixture display.

    """
    if fact.document.json_text == '"drop"':
        return ()
    return tuple(ProjectedEntry(
        entry_key=key, source_event_id=fact.event_id, entry_type="test.sample.card",
        document=fact.document, summary="Next state.", occurred_at=fact.occurred_at,
    ) for key in ("summary", "detail"))


def record_change(request: projections.ProjectionRequest, fact: events.ExtensionFact) -> RecordChange:
    """Propose a write from the captured revision, never from live storage.

    Returns:
        A replacement, restore, or explicit deletion proposal.

    """
    captured = request.prior_records[0]
    if fact.document.json_text == '"delete"':
        return DeleteRecord(key=captured.key, expected_revision=captured.revision)
    revision = 0 if fact.document.json_text == '"stale"' else captured.revision
    return PutRecord(key=captured.key, expected_revision=revision, document=fact.document, summary="Next state.")


class ProjectionExample(plugin.ExtensionPlugin, lifecycle.ExtensionLifecycle, projection.ExtensionProjector):
    """Keep pure projection and package lifetime behind explicit protocols."""

    def __init__(self, host_services: services.ExtensionHostServices) -> None:
        """Keep public host services without importing host implementation code."""
        self._services = host_services

    @property
    def extension_info(self) -> lifecycle_models.ExtensionInfo:
        """The exact package identity selected by the host."""
        return self._services.environment.extension_info

    @property
    def capabilities(self) -> plugin.ExtensionCapabilities:
        """The two capabilities owned by this package."""
        return plugin.ExtensionCapabilities(lifecycle=self, projector=self)

    def activate(self, request: lifecycle_models.ActivationRequest) -> lifecycle_models.ActivationResult:
        """Activate the fixture.

        Returns:
            Readiness for the selected runtime.

        """
        return lifecycle_models.ActivationReady(runtime_revision=request.runtime_revision)

    def deactivate(self, request: lifecycle_models.DeactivationRequest) -> lifecycle_models.DeactivationResult:
        """Stop the fixture with no live resources.

        Returns:
            Completion for the selected runtime.

        """
        return lifecycle_models.DeactivationResult(runtime_revision=request.runtime_revision)

    def select_records(
        self, selection_request: projections.ProjectionSelectionRequest,
    ) -> projections.ProjectionReadSet:
        """Select a needed key without a live host call.

        Returns:
            One key for nonempty input and no keys for empty input.

        """
        keys = (selected_key(selection_request),) if selection_request.events else ()
        return projections.ProjectionReadSet(binding=selection_request.binding, keys=keys)

    def project(self, projection_request: projections.ProjectionRequest) -> projections.ProjectionResult:
        """Use only captured fact bodies and record state.

        Returns:
            A complete feature-owned proposal.

        """
        if not projection_request.events:
            return projections.ProjectionResult(binding=projection_request.binding)
        fact = projection_request.events[-1].fact
        if not isinstance(fact, events.ExtensionFact):
            return projections.ProjectionResult(binding=projection_request.binding)
        if fact.document.json_text == '"host_call"':
            self._services.directory.list_extensions(directory.DirectoryRequest(active_only=True))
        return projections.ProjectionResult(
            binding=projection_request.binding, entries=feed_entries(fact),
            record_changes=(record_change(projection_request, fact),),
        )


def build_extension(services: services.ExtensionHostServices) -> plugin.ExtensionPlugin:
    """Load this factory only inside the SDK worker.

    Returns:
        The complete external projection fixture.

    """
    return ProjectionExample(services)


FACTORY: plugin.ExtensionFactory = build_extension
