# Copyright (c) 2026 Zhambyl Yermagambet
"""Retain one active runtime through all stages of an engine batch."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from extensions.interpretation_resources import InterpretationStores
from extensions.models.source_processing import SourcePolicy
from extensions.processing_batch import SelectedProcessingBatch
from extensions.processing_contract import ExtensionProcessing, ExtensionProcessingBatch
from extensions.source_batch import SelectedSourceBatch
from extensions.source_resources import SourceCallbacks, SourcePlans, SourceServices
from extensions.source_selection import SourceBatchContext


@dataclass(frozen=True)
class ProcessingRuntime(ExtensionProcessing):
    """Own only data-only plans between passes; the manager owns all worker resources."""

    services: SourceServices
    callbacks: SourceCallbacks
    stores: InterpretationStores
    policy: SourcePolicy = field(default_factory=SourcePolicy)
    plans: SourcePlans = field(default_factory=SourcePlans)

    @contextmanager
    def capture_batch(self) -> Iterator[ExtensionProcessingBatch | None]:
        """Keep publication busy until source reads, interpretation, and core reactions finish.

        Yields:
            A borrowed batch, or no extension processing after a failed initial restore.

        Raises:
            RuntimeError: If the manager and registry do not select the same active runtime.

        """
        with self.services.registry.read_snapshot() as selected:
            state = self.services.manager.read_state()
            if state.active_runtime is None:
                yield None
                return
            revision = selected.snapshot.directory.runtime_revision
            if state.active_runtime != revision or state.lifecycle.manager_id is None:
                message = "processing batch does not match the active manager runtime"
                raise RuntimeError(message)
            self.plans.select_runtime(revision)
            context = SourceBatchContext(
                state.lifecycle.manager_id, selected.snapshot, self.services.scopes.source_scopes(),
            )
            try:
                yield SelectedProcessingBatch(
                    SelectedSourceBatch(context, self.services, self.callbacks, self.plans, self.policy), self.stores,
                )
            finally:
                if context.scopes != self.services.scopes.source_scopes():
                    self.callbacks.changed()
