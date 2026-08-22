"""The last model and effort a translator saw, so that a repeat is not a change.

Both harnesses report the CURRENT model on every model response and the current
effort on every mid-turn hook. Recorded verbatim that produced 6,642 stored
`model.changed` rows on one machine, nearly all of them saying exactly what the
row before them said — and every one of them with `previous` empty, because a
single observation cannot know what it replaced.

A change event has to carry a change. That needs memory, and it cannot be the
store's: the store dedups by event id forever, which cannot represent a switch
to B and back to A.

Shared by both harness translators, and shared HERE for the same reason
`canonical_event` is: it is a rule about the facts, not about any harness's
grammar. Memory is per process — the first observation after a restart is a
change with no previous, which is one event per restart rather than one per
response.
"""

from __future__ import annotations

from domain.events import EffortChanged, ModelChanged
from domain.ids import ActorId, SessionId
from domain.values import EffortChangeReason, ModelChangeReason, ModelReference


class SelectionSemantics:
    def __init__(self) -> None:
        self._models: dict[tuple[str, str], tuple[ModelReference, str]] = {}
        self._efforts: dict[tuple[str, str], str] = {}

    @staticmethod
    def _key(session_id: SessionId, actor_id: ActorId) -> tuple[str, str]:
        return (str(session_id), str(actor_id))

    def model(
        self,
        session_id: SessionId,
        actor_id: ActorId,
        model_reference: ModelReference,
        model_change_reason: ModelChangeReason,
        equivalence_key: str,
    ) -> ModelChanged | None:
        """The switch this observation reports, or None when it reports no switch.

        The adapter supplies the key used to compare its aliases and resolved
        names. That vendor rule does not leak into the canonical reference.
        """
        key = self._key(session_id, actor_id)
        previous_observation = self._models.get(key)
        if previous_observation is not None and previous_observation[1] == equivalence_key:
            return None
        previous = previous_observation[0] if previous_observation is not None else None
        self._models[key] = (model_reference, equivalence_key)
        return ModelChanged(previous, model_reference, model_change_reason)

    def effort(
        self,
        session_id: SessionId,
        actor_id: ActorId,
        current: str,
        effort_change_reason: EffortChangeReason,
    ) -> EffortChanged | None:
        key = self._key(session_id, actor_id)
        previous = self._efforts.get(key)
        if previous == current:
            return None
        self._efforts[key] = current
        return EffortChanged(previous, current, effort_change_reason)
