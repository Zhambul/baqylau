# Copyright (c) 2026 Zhambyl Yermagambet
"""Process mixed pending input against the same retained runtime as source reads."""

from collections.abc import Callable
from dataclasses import dataclass

from baqylau_extension_api.models.canonical import CoreStateSnapshot

from extensions import (
    interpretation_checks,
    interpretation_contract,
    interpretation_pipeline,
    interpretation_resources,
    interpretation_selection,
)
from extensions.models.interpretation_context import InterpretationContext
from extensions.models.interpretation_snapshot import PriorStateRequest
from extensions.models.interpretations import InterpretationCommit, InterpretationProposal
from extensions.models.observations import StoredObservation
from extensions.processing_contract import ExtensionProcessingBatch
from extensions.registry_package import RegistryPackage
from extensions.source_batch import SelectedSourceBatch
from extensions.source_processing_contract import ExtensionSourceWatches

INTERPRETATION_BATCH_SIZE = 100


@dataclass(frozen=True)
class SelectedProcessingBatch(ExtensionProcessingBatch):
    """Borrow source capabilities and storage; own no worker or registry lifetime."""

    sources: SelectedSourceBatch
    stores: interpretation_resources.InterpretationStores

    @property
    def packages(self) -> tuple[RegistryPackage, ...]:
        """The active runtime's ordered packages."""
        return self.sources.context.snapshot.packages

    def read_sources(
        self, watches: ExtensionSourceWatches, stopped: Callable[[], bool], *, refresh_plans: bool,
    ) -> float | None:
        """Delegate bounded source reads under the complete batch's registry read.

        Returns:
            The next source deadline, if any.

        """
        return self.sources.read_sources(watches, stopped, refresh_plans=refresh_plans)

    def interpret_pending(
        self, core: interpretation_contract.CoreInterpretation, stopped: Callable[[], bool], *,
        yield_requested: Callable[[], bool] = bool,
    ) -> int:
        """Keep actual mixed arrival order and commit each original before the next.

        Returns:
            The number of complete interpretations, including suppressed or failed input.

        """
        completed = 0
        for original in self.stores.observations.pending_observations(INTERPRETATION_BATCH_SIZE):
            if stopped() or (completed > 0 and yield_requested()):
                break
            proposal = self._proposal(original, core)
            outcome = self.stores.facts.record_interpretation(InterpretationCommit(
                proposal=proposal, completed_at=self.sources.callbacks.clock(),
            ))
            core.accept_interpretation(original, outcome)
            completed += 1
        return completed

    def _proposal(
        self, original: StoredObservation, core: interpretation_contract.CoreInterpretation,
    ) -> InterpretationProposal:
        selected = self.sources.context
        context = interpretation_selection.capture_context(
            selected.manager_id, selected.snapshot, original, self.stores.facts.current_fact_page(0, 1),
        )
        packages = tuple(
            package for owner in selected.snapshot.active_order for package in selected.snapshot.packages
            if package.manifest.extension_id == owner
        )
        return interpretation_pipeline.InterpretationPipeline(
            interpretation_checks.InterpretationChecks(context, self.stores), packages, core, self._prior(context),
        ).interpret()

    def _prior(self, context: InterpretationContext) -> CoreStateSnapshot:
        capabilities = (package.manifest.capabilities for package in context.packages.values())
        if not any("canonical_transformer" in selected for selected in capabilities):
            return CoreStateSnapshot(after_cursor=context.binding.expected_canonical_cursor)
        return self.stores.facts.capture_prior_state(PriorStateRequest(
            history_revision=context.binding.history_revision, scope=context.binding.scope,
            expected_canonical_cursor=context.binding.expected_canonical_cursor,
        ))
