# Copyright (c) 2026 Zhambyl Yermagambet
"""Expose complete engine processing through one borrowed runtime contract."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Protocol

from extensions.interpretation_contract import CoreInterpretation
from extensions.registry_package import RegistryPackage
from extensions.source_processing_contract import ExtensionSourceBatch


class ExtensionProcessingBatch(ExtensionSourceBatch, Protocol):
    """Keep source calls and mixed interpretation in one active runtime."""

    @property
    def packages(self) -> tuple[RegistryPackage, ...]:
        """The active runtime's ordered packages."""
        ...

    def interpret_pending(
        self, core: CoreInterpretation, stopped: Callable[[], bool], *,
        yield_requested: Callable[[], bool] = bool,
    ) -> int:
        """Check stop before each original, but timed yield only after the first.

        Neither predicate interrupts an interpretation transaction. A timed
        yield cannot prevent all progress. The caller owns continuation notices.
        """
        ...


class ExtensionProcessing(Protocol):
    """Retain one runtime until all stages and core reactions in the pass finish."""

    def capture_batch(self) -> AbstractContextManager[ExtensionProcessingBatch | None]:
        """Borrow the actual active capabilities, or no batch after a failed initial restore."""
        ...
