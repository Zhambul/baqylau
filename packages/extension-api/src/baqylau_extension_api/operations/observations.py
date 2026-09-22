# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate emitted observations before a host allocates raw source records."""

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest import lookup, rules
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models.observations import ObservationCandidate
from baqylau_extension_api.models.scopes import ExtensionScope
from baqylau_extension_api.operations.documents import require_document_schema
from baqylau_extension_api.schemas import SchemaSet


def validate_observations(
    manifest: ExtensionManifest, schemas: SchemaSet, scope: ExtensionScope,
    observations: tuple[ObservationCandidate, ...],
) -> None:
    """Keep result observations within the selected source owner and scope.

    Raises:
        ExtensionContractError: If an observation changes scope or uses an undeclared source.

    """
    rules.require_unique(
        ((source.source_identity, source.observation_key) for source in observations), "observation source keys",
    )
    for observation in observations:
        if observation.scope != scope:
            message = "result observation changed its accepted operation scope"
            raise ExtensionContractError(message)
        definition = lookup.source_definition(manifest, observation.source_type, scope.kind)
        require_document_schema(observation.document, definition.schema_ref, schemas, "observation document")
