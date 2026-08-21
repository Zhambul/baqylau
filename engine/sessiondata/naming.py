"""THE display name of a model — one owner per harness, applied at fold time.

Three surfaces used to derive a model's name independently: the catalog (the
picker), the actor row, and the feed entry. Each derived it from whatever
fields it held at that moment, so one model showed as "sonnet" in the picker,
"sonnet-5" on a refined actor, and either on an entry, depending on when it
was written. Now each harness names its models ONCE
(`HarnessPlugin.model_display`), the writers apply that answer when a fact
folds, and `rebuild` re-derives every historical row through the same
function — which is what makes a naming fix reach old sessions too.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace

from domain.ids import HarnessName
from domain.values import ModelReference


class ModelNaming:
    """The per-harness namers, with the honest fallback for harnesses that
    declare none: the display the source gave, or the native id."""

    def __init__(
        self,
        display_by_harness: Mapping[str, Callable[[ModelReference], str]] | None = None,
    ) -> None:
        self.display_by_harness = dict(display_by_harness or {})

    def display(self, harness: HarnessName, model_reference: ModelReference) -> str:
        namer = self.display_by_harness.get(harness)
        if namer is not None:
            return namer(model_reference)
        return model_reference.display_name or model_reference.native_id

    def named(self, harness: HarnessName, model_reference: ModelReference) -> ModelReference:
        """The same reference with its display settled — what the actor row
        stores, so every reader downstream shows the one name."""
        return replace(
            model_reference, display_name=self.display(harness, model_reference)
        )
