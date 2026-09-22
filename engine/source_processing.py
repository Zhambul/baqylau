# Copyright (c) 2026 Zhambyl Yermagambet
"""Connect extension source work to native watches and its own deadline notice."""

from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from pathlib import Path

from core.input_events import InputEvents
from core.input_paths import InputGroup, resolved_inputs
from core.work_queue import WorkKind, WorkQueue
from extensions.manager_contract import ExtensionRuntimeBoundary
from extensions.processing_contract import ExtensionProcessing, ExtensionProcessingBatch
from extensions.projection_pass import ProjectionPass
from extensions.source_processing_contract import (
    ExtensionSourceBatch,
    ExtensionSourceWatches,
)

SOURCE_DEADLINE_KEY = "extension source deadline"


@dataclass(frozen=True)
class EngineExtensionServices(ExtensionProcessing):
    """Group optional runtime publication and source processing for standalone engine consumers."""

    runtime: ExtensionRuntimeBoundary | None = None
    processing: ExtensionProcessing | None = None
    projections: ProjectionPass | None = None

    def capture_batch(self) -> AbstractContextManager[ExtensionProcessingBatch | None]:
        """Keep the normal core-only engine path free from dummy extension services.

        Returns:
            The real retained runtime context, or an explicit absent source batch.

        """
        return nullcontext(None) if self.processing is None else self.processing.capture_batch()


@dataclass(frozen=True)
class EngineSourceReads(ExtensionSourceWatches):
    """Change no core subscription or independently owned deadline."""

    inputs: InputEvents
    queue: WorkQueue
    clock: Callable[[], float]

    def watch_sources(self, paths: frozenset[Path]) -> None:
        """Keep source watches separate from the core puller's file set."""
        self.inputs.watch_files(set(resolved_inputs(paths)), input_group=InputGroup.ADDITIONAL)

    def read(
        self, batch: ExtensionSourceBatch | None, stopped: Callable[[], bool], *, refresh_plans: bool,
    ) -> None:
        """Replace the current source deadline after all successful and failed reads."""
        deadline = None
        if batch is None:
            self.watch_sources(frozenset())
        else:
            deadline = batch.read_sources(self, stopped, refresh_plans=refresh_plans)
        delay = None if deadline is None else deadline - self.clock()
        self.queue.set_deadline(WorkKind.EXTENSION_SOURCES, delay, SOURCE_DEADLINE_KEY)
