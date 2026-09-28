# Copyright (c) 2026 Zhambyl Yermagambet
"""Select the enabled projection transforms and apply them to a projection proposal."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from baqylau_extension_api.contracts import projection
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models import (
    projection_changes as change_models,
    projection_transforms,
    projections,
)
from baqylau_extension_api.projection_transform import results as transform_results
from baqylau_extension_api.schemas import SchemaSet

from extensions import processing_selection as selection, projection_models
from extensions.models import interpretations, scope_relations

if TYPE_CHECKING:
    from collections.abc import Sequence

    from extensions.registry_package import RegistryPackage


@dataclass(frozen=True)
class ProjectionTransformerPackage:
    """Keep one enabled package's projection transform and its declarations."""

    extension_id: str
    runtime_revision: str
    settings: scope_relations.ScopedSettings
    manifest: ExtensionManifest
    schemas: SchemaSet
    transformer: projection.ExtensionProjectionTransformer


def projection_transformer_packages(
    registry_packages: Sequence[RegistryPackage],
) -> tuple[ProjectionTransformerPackage, ...]:
    """Select the enabled packages which declare a projection transform.

    Returns:
        The transform packages in their active order.

    """
    selected: list[ProjectionTransformerPackage] = []
    for package in registry_packages:
        plugin = package.plugin
        if plugin is None or plugin.capabilities.projection_transformer is None:
            continue
        selected.append(ProjectionTransformerPackage(
            extension_id=package.manifest.extension_id,
            runtime_revision="" if package.environment is None else package.environment.runtime_revision,
            settings=package.resolved_settings,
            manifest=package.manifest,
            schemas=SchemaSet(package.manifest.schemas),
            transformer=plugin.capabilities.projection_transformer,
        ))
    return tuple(selected)


def transform_changes(
    transformer: ProjectionTransformerPackage, transform_request: projection_transforms.ProjectionTransformRequest,
) -> tuple[change_models.ProjectionChange, ...]:
    """Call one projection transform and check its operations against the proposal.

    Returns:
        The transformed changes.

    """
    result = transformer.transformer.transform(transform_request)
    return transform_results.apply_projection_transform(
        transformer.manifest, transform_request, result, transformer.schemas,
    )


def apply_transforms(
    transformers: Sequence[ProjectionTransformerPackage],
    request: projections.ProjectionRequest,
    facts: tuple[interpretations.StoredCanonicalFact, ...],
    changes: tuple[change_models.ProjectionChange, ...],
) -> tuple[change_models.ProjectionChange, ...]:
    """Apply every enabled transform that selects this scope and one of these facts.

    Returns:
        The transformed changes.

    """
    scope_kind = request.binding.context.scope.kind
    for transformer in transformers:
        transformer_selection = selection.scope_selection(
            transformer.manifest, selection.FactCapability.PROJECTION_TRANSFORMER, scope_kind,
        )
        selected = selection.selected_facts(transformer_selection, facts)
        if selected:
            changes = _transform(transformer, request, selected, changes)
    return changes


def _transform(
    transformer: ProjectionTransformerPackage,
    request: projections.ProjectionRequest,
    selected: tuple[interpretations.StoredCanonicalFact, ...],
    changes: tuple[change_models.ProjectionChange, ...],
) -> tuple[change_models.ProjectionChange, ...]:
    """Apply one enabled projection transform to the complete proposal.

    Returns:
        The transformed changes.

    """
    request_binding = request.binding
    binding = projection_models.projection_binding(
        transformer, request_binding.snapshot, request_binding.context.input_cursor,
    )
    transform_request = projection_transforms.ProjectionTransformRequest(
        binding=binding,
        events=tuple(stored.committed() for stored in selected),
        prior_records=request.prior_records,
        changes=changes,
    )
    return transform_changes(transformer, transform_request)
