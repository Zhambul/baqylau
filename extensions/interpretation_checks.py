# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject a complete invalid worker result before it changes the current batch."""

from dataclasses import dataclass
from graphlib import TopologicalSorter

from baqylau_extension_api.models.canonical import CanonicalFact

from domain.ids import CanonicalEventId, RawEventId
from extensions.interpretation_resources import InterpretationStores
from extensions.models import interpretation_facts as rules
from extensions.models.interpretation_context import InterpretationContext


@dataclass(frozen=True)
class InterpretationChecks:
    """Read only stored facts; final acceptance repeats these checks in its transaction."""

    context: InterpretationContext
    stores: InterpretationStores

    def facts(self, proposed: tuple[CanonicalFact, ...]) -> None:
        """Check every retained proposal, not only final visible output."""
        known: dict[str, CanonicalFact] = {}
        for fact in proposed:
            rules.validate_fact(self.context, fact)
            history = self.context.binding.history_revision
            stored = self.stores.facts.find_fact(history, CanonicalEventId(fact.event_id))
            if stored is not None:
                rules.require_same_identity(stored.fact, fact)
            rules.require_same_identity(known.setdefault(fact.event_id, fact), fact)
        self._causes(proposed, frozenset(known))

    def _causes(self, proposed: tuple[CanonicalFact, ...], known: frozenset[str]) -> None:
        graph = rules.cause_graph(proposed)
        tuple(TopologicalSorter(graph).static_order())
        causes: set[str] = set().union(*graph.values()) if graph else set()
        for cause in causes - known:
            self._require_cause(cause)

    def _require_cause(self, cause: str) -> None:
        if self.stores.observations.find_observation(RawEventId(cause)) is not None:
            return
        history = self.context.binding.history_revision
        if self.stores.facts.find_fact(history, CanonicalEventId(cause)) is None:
            message = "canonical cause is not recorded in the selected history or interpretation"
            raise ValueError(message)
