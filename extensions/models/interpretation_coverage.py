# Copyright (c) 2026 Zhambyl Yermagambet
"""Require a recorded step for every transform selected at its actual pipeline position."""

from dataclasses import dataclass

from baqylau_extension_api.models import canonical, events

from extensions.models import interpretation_selections as selection
from extensions.models.interpretation_context import InterpretationContext, ProcessingPackage


@dataclass
class TransformCoverage:
    """Advance once through the retained active order for one processing stage."""

    context: InterpretationContext
    position: int = -1

    def raw(self, owner: str | None, inputs: tuple[events.RawInput, ...]) -> None:
        """Reject skipped raw transforms that had selected input at this position.

        Raises:
            ValueError: If an eligible raw transform has no recorded step.

        """
        if any(selection.raw_inputs(package, inputs) for package in self._skipped(owner)):
            message = "interpretation omitted an eligible raw transform"
            raise ValueError(message)

    def canonical(self, owner: str | None, inputs: tuple[canonical.CanonicalFact, ...]) -> None:
        """Check canonical coverage after earlier transforms have changed the candidate set.

        Raises:
            ValueError: If an eligible canonical transform has no recorded step.

        """
        if any(selection.canonical_inputs(package, inputs) for package in self._skipped(owner)):
            message = "interpretation omitted an eligible canonical transform"
            raise ValueError(message)

    def _skipped(self, owner: str | None) -> tuple[ProcessingPackage, ...]:
        owners = tuple(self.context.packages)
        position = len(owners) if owner is None else owners.index(owner)
        if owner is not None and position <= self.position:
            message = "transform steps must follow runtime order without repeated owners"
            raise ValueError(message)
        skipped = owners[self.position + 1:position]
        self.position = position
        return tuple(self.context.packages[skipped_owner] for skipped_owner in skipped)
