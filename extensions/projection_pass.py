# Copyright (c) 2026 Zhambyl Yermagambet
"""Project accepted facts into extension entries and records outside transactions."""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Self

from baqylau_extension_api.models import projection_changes as change_models, projections, scopes
from baqylau_extension_api.projection import results as projection_results, selection as projection_selection

from domain.ids import SessionId
from extensions import pass_health, processing_selection as selection, projector_packages, transformer_packages
from extensions.models import interpretations
from extensions.projection_changes import committed_changes, projection_changes
from extensions.projection_models import GenerationHeads, ProjectionFacts, projection_binding, projection_snapshot
from extensions.registry_package import RegistryPackage
from repository.contract import extension_projections, extension_records, pending_scope_query, session_data

DEFAULT_HISTORY_REVISION = "default"
DEFAULT_GENERATION = "default"
PROJECTION_BATCH_SIZE = 500


@dataclass(frozen=True)
class ProjectionPass:
    """Run one package's pure projection for one scope, then commit once."""

    facts: ProjectionFacts
    record_reader: extension_records.RecordStateReader
    store: extension_projections.ExtensionProjectionRepository
    history_revision: str = DEFAULT_HISTORY_REVISION
    generation: str = DEFAULT_GENERATION
    batch_size: int = PROJECTION_BATCH_SIZE
    heads: GenerationHeads | None = None

    def run_selected(
        self,
        registry_packages: Sequence[RegistryPackage],
        limit: int,
        health: pass_health.PassHealth,
    ) -> int:
        """Project, for every enabled projector package, the declared scopes after its own live cursor.

        A package's first live pass starts it at the canonical head, so enable
        applies to future facts; a rebuild reads the past.

        Returns:
            The number of facts read across packages.

        """
        transformers = transformer_packages.projection_transformer_packages(registry_packages)
        total = 0
        for package in projector_packages.projector_packages(registry_packages):
            live = _live(self.heads, self.generation, package.extension_id)
            self.store.ensure_floor(package.extension_id, self.history_revision, live)
            total += self.for_generation(live).project_owner(package, transformers, limit, health)
        return total

    def for_generation(self, generation: str) -> Self:
        """Select the generation that this pass reads cursors of and writes to.

        Returns:
            The same pass for another generation.

        """
        return replace(self, generation=generation)

    def project_owner(
        self,
        package: projector_packages.ProjectorPackage,
        transformers: Sequence[transformer_packages.ProjectionTransformerPackage],
        limit: int,
        health: pass_health.PassHealth,
    ) -> int:
        """Project one package's declared scopes after its cursors of this pass's generation.

        Returns:
            The number of facts read.

        """
        pending = self.store.pending_scopes(pending_scope_query.PendingScopeQuery(
            owner=package.extension_id,
            scope_kinds=selection.declared_scope_kinds(package.manifest, selection.FactCapability.PROJECTOR),
            history_revision=self.history_revision,
            generation=self.generation,
            limit=limit,
        ))
        total = 0
        for scope in pending:
            try:
                total += self.run(package, scope, transformers)
            except Exception:  # noqa: BLE001 -- Record one projection failure and continue others.
                health.failed(package.extension_id)
            else:
                health.succeeded(package.extension_id)
        return total

    def run(
        self,
        package: projector_packages.ProjectorPackage,
        scope: scopes.ExtensionScope,
        transformers: Sequence[transformer_packages.ProjectionTransformerPackage] = (),
    ) -> int:
        """Project the facts after this package's cursor for one scope.

        The projector and each transform get only the facts that they select. A
        page with no selected fact commits only the cursor.

        Returns:
            The number of facts the projection consumed.

        """
        cursor = self.store.committed_cursor(package.extension_id, scope, self.history_revision, self.generation)
        page = self.facts.facts_for_scope(self.history_revision, scope, cursor, self.batch_size)
        if not page.facts:
            return 0
        last = page.facts[-1].cursor
        selected = _selected(package, scope, page.facts)
        changes = self._changes(package, scope, (cursor, last), selected, transformers)
        self.store.apply_projection(self._commit(package, scope, last, committed_changes(
            package.extension_id, scope, selected, changes,
        )))
        return len(page.facts)

    def _changes(
        self,
        package: projector_packages.ProjectorPackage,
        scope: scopes.ExtensionScope,
        cursors: tuple[int, int],
        facts: tuple[interpretations.StoredCanonicalFact, ...],
        transformers: Sequence[transformer_packages.ProjectionTransformerPackage],
    ) -> tuple[change_models.ProjectionChange, ...]:
        if not facts:
            return ()
        request = self._request(package, scope, cursors, facts)
        projected = _projected(package, scope, request)
        return transformer_packages.apply_transforms(transformers, request, facts, projected)

    def _request(
        self,
        package: projector_packages.ProjectorPackage,
        scope: scopes.ExtensionScope,
        cursors: tuple[int, int],
        facts: Sequence[interpretations.StoredCanonicalFact],
    ) -> projections.ProjectionRequest:
        snapshot_cursor, input_cursor = cursors
        snapshot = projection_snapshot(scope, self.history_revision, self.generation, snapshot_cursor)
        selection_request = projections.ProjectionSelectionRequest(
            binding=projection_binding(package, snapshot, input_cursor),
            events=tuple(stored.committed() for stored in facts),
        )
        read_set = projection_selection.validate_read_set(
            selection_request, package.projector.select_records(selection_request),
        )
        return projection_selection.capture_projection_request(
            selection_request, read_set, self.record_reader.record_states(read_set.keys),
        )

    def _commit(
        self,
        package: projector_packages.ProjectorPackage,
        scope: scopes.ExtensionScope,
        commit_cursor: int,
        changes: session_data.SessionDataChanges,
    ) -> extension_projections.ProjectionCommit:
        return extension_projections.ProjectionCommit(
            owner=package.extension_id,
            scope=scope,
            history_revision=self.history_revision,
            generation=self.generation,
            commit_cursor=commit_cursor,
            changes=changes,
            session_id=SessionId(scope.session_id) if scope.kind == "session" else None,
        )


def _live(heads: GenerationHeads | None, generation: str, owner: str) -> str:
    return generation if heads is None else heads.active_generation(owner)


def _selected(
    package: projector_packages.ProjectorPackage,
    scope: scopes.ExtensionScope,
    facts: Sequence[interpretations.StoredCanonicalFact],
) -> tuple[interpretations.StoredCanonicalFact, ...]:
    projector_selection = selection.scope_selection(package.manifest, selection.FactCapability.PROJECTOR, scope.kind)
    return selection.selected_facts(projector_selection, facts)


def _projected(
    package: projector_packages.ProjectorPackage, scope: scopes.ExtensionScope, request: projections.ProjectionRequest,
) -> tuple[change_models.ProjectionChange, ...]:
    projection_result = projection_results.validate_projection_result(request, package.projector.project(request))
    projection_results.validate_projection_documents(package.manifest, package.schemas, projection_result)
    return projection_changes(package.extension_id, scope, projection_result)
