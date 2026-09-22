# Copyright (c) 2026 Zhambyl Yermagambet
"""Own core display changes and feed replacement in an external SDK-only feature."""

from baqylau_extension_api.contracts import lifecycle, plugin, projection, services
from baqylau_extension_api.identities import DerivedIdentity, derived_projection_change_id
from baqylau_extension_api.models import directory, lifecycle as lifecycle_models, projection_changes as changes
from baqylau_extension_api.models.projection_transforms import ProjectionTransformRequest, ProjectionTransformResult
from baqylau_extension_api.models.record_changes import PutRecord
from baqylau_extension_api.models.transforms import Drop, Insert, Replace, TransformOperation


def display_change(change: changes.ProjectionChange) -> changes.ProjectionChange:
    """Change visible data while retaining source identity and execution fields.

    Returns:
        A complete replacement for one existing typed write proposal.

    """
    if isinstance(change, changes.CoreSessionChange):
        return change.model_copy(update={"session": change.session.model_copy(update={
            "title": "Extension title",
        })})
    if isinstance(change, changes.CoreActorChange):
        return change.model_copy(update={"actor": change.actor.model_copy(update={
            "name": "Extension actor",
        })})
    if isinstance(change, changes.ExtensionEntryChange):
        return change.model_copy(update={"entry": change.entry.model_copy(update={
            "summary": "Extension summary",
        })})
    if isinstance(change, changes.ExtensionRecordChange) and isinstance(change.write, PutRecord):
        document = change.write.document.model_copy(update={"json_text": '"Extension record"'})
        return change.model_copy(update={"write": change.write.model_copy(update={
            "document": document,
        })})
    return change


def feed_addition(
    original: changes.CoreEntryChange, template: changes.ExtensionEntryChange,
) -> Insert[changes.ProjectionChange]:
    """Replace the generic feed display with an owned schema-defined row.

    Returns:
        One stable extension row after the suppressed core anchor.

    """
    identity = DerivedIdentity(extension_id=template.owner, input_id=original.change_id, output_key="custom-feed")
    change_id = derived_projection_change_id(identity)
    row = template.entry.model_copy(update={"entry_key": change_id, "source_event_id": original.source_event_id})
    return Insert(input_id=original.change_id, output_key=identity.output_key, position="after", document=(
        template.model_copy(update={"change_id": change_id, "entry": row})
    ))


def operations(request: ProjectionTransformRequest) -> tuple[TransformOperation[changes.ProjectionChange], ...]:
    """Build all feature operations without reading live host state.

    Returns:
        Ordered explicit display changes and an owned feed insertion.

    """
    template = next((
        entry for entry in request.changes if isinstance(entry, changes.ExtensionEntryChange)
    ), None)
    output: list[TransformOperation[changes.ProjectionChange]] = []
    for change in request.changes:
        if isinstance(change, changes.CoreEntryChange) and template is not None:
            output.extend((
                Drop(input_id=change.change_id, reason="Use the extension feed view"), feed_addition(change, template),
            ))
        else:
            output.append(Replace[changes.ProjectionChange](
                input_id=change.change_id, document=display_change(change),
            ))
    return tuple(output)


class TransformExample(plugin.ExtensionPlugin, lifecycle.ExtensionLifecycle, projection.ExtensionProjectionTransformer):
    """Implement only the capabilities declared by this external feature."""

    def __init__(self, host_services: services.ExtensionHostServices) -> None:
        """Keep public host services without a private application import."""
        self._services = host_services

    @property
    def extension_info(self) -> lifecycle_models.ExtensionInfo:
        """The host-selected package identity."""
        return self._services.environment.extension_info

    @property
    def capabilities(self) -> plugin.ExtensionCapabilities:
        """The exact public capabilities of this package."""
        return plugin.ExtensionCapabilities(lifecycle=self, projection_transformer=self)

    def activate(self, request: lifecycle_models.ActivationRequest) -> lifecycle_models.ActivationResult:
        """Activate the fixture without live resources.

        Returns:
            Readiness for the requested runtime.

        """
        return lifecycle_models.ActivationReady(runtime_revision=request.runtime_revision)

    def deactivate(self, request: lifecycle_models.DeactivationRequest) -> lifecycle_models.DeactivationResult:
        """Stop the fixture without pending live work.

        Returns:
            Completion for the requested runtime.

        """
        return lifecycle_models.DeactivationResult(runtime_revision=request.runtime_revision)

    def transform(self, projection_request: ProjectionTransformRequest) -> ProjectionTransformResult:
        """Transform captured proposals without live effects or storage writes.

        Returns:
            Explicit operations for the exact supplied snapshot.

        """
        if not projection_request.changes:
            self._services.directory.list_extensions(directory.DirectoryRequest(active_only=True))
        return ProjectionTransformResult(binding=projection_request.binding, operations=operations(projection_request))


def build_extension(services: services.ExtensionHostServices) -> plugin.ExtensionPlugin:
    """Build the feature only inside its isolated worker.

    Returns:
        The SDK-only fixture plugin.

    """
    return TransformExample(services)


FACTORY: plugin.ExtensionFactory = build_extension
