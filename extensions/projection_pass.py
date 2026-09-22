# Copyright (c) 2026 Zhambyl Yermagambet
"""Project accepted facts into extension entries and records outside transactions."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from baqylau_extension_api.models import (
    canonical,
    projection_changes as change_models,
    projection_transforms,
    projections,
    scopes,
)
from baqylau_extension_api.projection import results as projection_results, selection as projection_selection
from baqylau_extension_api.projection_transform import results as transform_results
from baqylau_extension_api.schemas import SchemaSet

from domain.ids import SessionId
from extensions.models import interpretations
from extensions.projection_changes import committed_changes, projection_changes
from extensions.projection_models import (
    ProjectionFacts,
    ProjectionTransformerPackage,
    ProjectorPackage,
    projection_binding,
    projection_snapshot,
)
from extensions.registry_package import RegistryPackage
from repository.contract import extension_projections, extension_records

DEFAULT_HISTORY_REVISION = "default"
DEFAULT_GENERATION = "default"
PROJECTION_BATCH_SIZE = 500


@dataclass(frozen=True)
class ProjectionPass:
    """Run one package's pure projection for one scope, then commit once."""

    facts: ProjectionFacts
    record_reader: extension_records.ExtensionRecordRepository
    store: extension_projections.ExtensionProjectionRepository
    history_revision: str = DEFAULT_HISTORY_REVISION
    generation: str = DEFAULT_GENERATION
    batch_size: int = PROJECTION_BATCH_SIZE

    def run_selected(
        self,
        registry_packages: Sequence[RegistryPackage],
        after_cursor: int,
        limit: int,
        on_failure: Callable[[], None],
    ) -> int:
        """Project every enabled projector package for every scope with new facts.

        Returns:
            The number of facts projected across packages.

        """
        packages = self.packages(registry_packages)
        if not packages:
            return 0
        transformers = self.transformers(registry_packages)
        total = 0
        for scope in self.store.scopes_after(self.history_revision, after_cursor, limit):
            for package in packages:
                try:
                    total += self.run(package, scope, transformers)
                except Exception:  # noqa: BLE001 -- Record one projection failure and continue others.
                    on_failure()
        return total

    def packages(self, registry_packages: Sequence[RegistryPackage]) -> tuple[ProjectorPackage, ...]:
        """Select the enabled packages which declare a projector.

        Returns:
            The projector packages in their active order.

        """
        selected: list[ProjectorPackage] = []
        for package in registry_packages:
            plugin = package.plugin
            if plugin is None or plugin.capabilities.projector is None:
                continue
            selected.append(ProjectorPackage(
                extension_id=package.manifest.extension_id,
                runtime_revision="" if package.environment is None else package.environment.runtime_revision,
                settings_revision=package.settings.revision,
                manifest=package.manifest,
                schemas=SchemaSet(package.manifest.schemas),
                projector=plugin.capabilities.projector,
            ))
        return tuple(selected)

    def transformers(
        self, registry_packages: Sequence[RegistryPackage],
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
                settings_revision=package.settings.revision,
                manifest=package.manifest,
                schemas=SchemaSet(package.manifest.schemas),
                transformer=plugin.capabilities.projection_transformer,
            ))
        return tuple(selected)

    def run(
        self,
        package: ProjectorPackage,
        scope: scopes.ExtensionScope,
        transformers: Sequence[ProjectionTransformerPackage] = (),
    ) -> int:
        """Project the facts after this package's cursor for one scope.

        Returns:
            The number of facts the projection consumed.

        """
        cursor = self.store.committed_cursor(package.extension_id, scope, self.history_revision, self.generation)
        page = self.facts.facts_for_scope(self.history_revision, scope, cursor, self.batch_size)
        if not page.facts:
            return 0
        snapshot = projection_snapshot(scope, self.history_revision, self.generation, cursor)
        binding = projection_binding(package, snapshot, page.facts[-1].cursor)
        changes = self._changes(package, scope, binding, _committed(page.facts), transformers)
        self.store.apply_projection(extension_projections.ProjectionCommit(
            owner=package.extension_id,
            scope=scope,
            history_revision=self.history_revision,
            generation=self.generation,
            commit_cursor=page.facts[-1].cursor,
            changes=committed_changes(package.extension_id, scope, page.facts, changes),
            session_id=SessionId(scope.session_id) if scope.kind == "session" else None,
        ))
        return len(page.facts)

    def _changes(
        self,
        package: ProjectorPackage,
        scope: scopes.ExtensionScope,
        binding: projections.ProjectionBinding,
        committed: tuple[canonical.CommittedFact, ...],
        transformers: Sequence[ProjectionTransformerPackage],
    ) -> tuple[change_models.ProjectionChange, ...]:
        selection_request = projections.ProjectionSelectionRequest(binding=binding, events=committed)
        selection = projection_selection.validate_read_set(
            selection_request, package.projector.select_records(selection_request),
        )
        request = projection_selection.capture_projection_request(
            selection_request, selection, self.record_reader.record_states(selection.keys),
        )
        result = projection_results.validate_projection_result(request, package.projector.project(request))
        projection_results.validate_projection_documents(package.manifest, package.schemas, result)
        return _apply_transforms(
            transformers, request, committed, projection_changes(package.extension_id, scope, result),
        )


def _apply_transforms(
    transformers: Sequence[ProjectionTransformerPackage],
    request: projections.ProjectionRequest,
    committed: tuple[canonical.CommittedFact, ...],
    changes: tuple[change_models.ProjectionChange, ...],
) -> tuple[change_models.ProjectionChange, ...]:
    """Apply every enabled transform to the complete proposal.

    Returns:
        The transformed changes.

    """
    for transformer in transformers:
        changes = _transform(transformer, request, committed, changes)
    return changes


def _transform(
    transformer: ProjectionTransformerPackage,
    request: projections.ProjectionRequest,
    committed: tuple[canonical.CommittedFact, ...],
    changes: tuple[change_models.ProjectionChange, ...],
) -> tuple[change_models.ProjectionChange, ...]:
    """Apply one enabled projection transform to the complete proposal.

    Returns:
        The transformed changes.

    """
    binding = projection_binding(transformer, request.binding.snapshot, request.binding.context.input_cursor)
    transform_request = projection_transforms.ProjectionTransformRequest(
        binding=binding,
        events=committed,
        prior_records=request.prior_records,
        changes=changes,
    )
    result = transformer.transformer.transform(transform_request)
    return transform_results.apply_projection_transform(
        transformer.manifest, transform_request, result, transformer.schemas,
    )


def _committed(facts: Sequence[interpretations.StoredCanonicalFact]) -> tuple[canonical.CommittedFact, ...]:
    return tuple(
        canonical.CommittedFact(fact=stored.fact, cursor=stored.cursor, accepted_at=stored.accepted_at)
        for stored in facts
    )
