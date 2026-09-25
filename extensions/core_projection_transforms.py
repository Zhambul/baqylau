# Copyright (c) 2026 Zhambyl Yermagambet
"""Run enabled projection transforms over one core event's proposed rows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from baqylau_extension_api.models import canonical, projection_changes, projection_transforms

from engine.sessiondata.contract import AggregateState, CoreChangeTransform
from extensions import (
    core_changes,
    pass_health,
    processing_selection as selection,
    projection_models,
    transformer_packages,
)
from extensions.mapper import core_events

if TYPE_CHECKING:
    from collections.abc import Sequence

    from baqylau_extension_api.core.aggregate import CoreAggregateState

    from domain.event_base import CanonicalEvent, EventPayload
    from extensions.registry_package import RegistryPackage
    from repository.contract.session_data import SessionDataChanges

DEFAULT_REVISION = "default"


@dataclass(frozen=True)
class CoreProjectionTransforms(CoreChangeTransform):
    """Apply every selecting transform in active order; keep core progress on any rejection."""

    transformers: tuple[transformer_packages.ProjectionTransformerPackage, ...]
    health: pass_health.PassHealth
    history_revision: str = DEFAULT_REVISION
    generation: str = DEFAULT_REVISION

    def transform(
        self,
        canonical_event: CanonicalEvent[EventPayload],
        before_aggregate_state: AggregateState,
        proposed_session_data_changes: SessionDataChanges,
    ) -> SessionDataChanges:
        """Return the changes to commit after every selecting transform.

        Returns:
            The transformed changes, or the changes before a failed transform.

        """
        committed = core_events.public_committed(canonical_event)
        selecting = self._selecting(committed)
        if not selecting:
            return proposed_session_data_changes
        proposal = core_changes.proposed_changes(proposed_session_data_changes, committed.fact.event_id)
        state = core_changes.before_core(before_aggregate_state)
        for transformer in selecting:
            proposal = self._apply(transformer, committed, state, proposal)
        return core_changes.committed_core_changes(committed, self.history_revision, proposal)

    def _selecting(
        self, committed: canonical.CommittedFact,
    ) -> tuple[transformer_packages.ProjectionTransformerPackage, ...]:
        kind = canonical.fact_type(committed.fact)
        selected: list[transformer_packages.ProjectionTransformerPackage] = []
        for transformer in self.transformers:
            session_selection = selection.scope_selection(
                transformer.manifest, selection.FactCapability.PROJECTION_TRANSFORMER, "session",
            )
            if session_selection is not None and kind in session_selection.input_types:
                selected.append(transformer)
        return tuple(selected)

    def _apply(
        self,
        transformer: transformer_packages.ProjectionTransformerPackage,
        committed: canonical.CommittedFact,
        state: CoreAggregateState,
        proposal: tuple[projection_changes.ProjectionChange, ...],
    ) -> tuple[projection_changes.ProjectionChange, ...]:
        snapshot = projection_models.projection_snapshot(
            committed.fact.scope, self.history_revision, self.generation, committed.cursor - 1,
        )
        request = projection_transforms.ProjectionTransformRequest(
            binding=projection_models.projection_binding(transformer, snapshot, committed.cursor),
            events=(committed,),
            before_core=state,
            prior_records=(),
            changes=proposal,
        )
        try:
            changes = transformer_packages.transform_changes(transformer, request)
        except Exception:  # noqa: BLE001 -- A rejected extension result never stops core progress.
            self.health.failed(transformer.extension_id)
            return proposal
        self.health.succeeded(transformer.extension_id)
        return changes


def core_transforms(
    registry_packages: Sequence[RegistryPackage], health: pass_health.PassHealth,
) -> CoreProjectionTransforms | None:
    """Build the core row transform of the enabled projection transforms for one captured batch.

    Returns:
        The transform, or None when no package declares a projection transform.

    """
    transformers = transformer_packages.projection_transformer_packages(registry_packages)
    return CoreProjectionTransforms(transformers, health) if transformers else None
