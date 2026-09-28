# Copyright (c) 2026 Zhambyl Yermagambet
"""Separate session-bound core facts from explicitly scoped extension facts."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from baqylau_extension_api.core.payloads import CorePayload
from baqylau_extension_api.models.base import OpaqueId, Revision, WireModel
from baqylau_extension_api.models.events import ExtensionFact
from baqylau_extension_api.models.scopes import SessionScope


class CoreFact(WireModel):
    """Carry a core candidate without host-owned acceptance metadata."""

    kind: Literal["core"] = "core"
    event_id: OpaqueId
    scope: SessionScope
    payload: CorePayload
    turn_id: OpaqueId | None = None
    parent_actor_id: OpaqueId | None = None
    occurred_at: float | None = None
    terminal_window_id: OpaqueId | None = None
    harness_process_id: int | None = None
    raw_event_ids: tuple[OpaqueId, ...] = ()

    @model_validator(mode="after")
    def validate_parent(self) -> Self:
        """Reject an actor that names itself as its parent.

        Returns:
            The validated core candidate.

        Raises:
            ValueError: If the actor and parent identities are equal.

        """
        if self.parent_actor_id == self.scope.actor_id:
            message = "an actor cannot be its own parent"
            raise ValueError(message)
        return self


type CanonicalFact = Annotated[CoreFact | ExtensionFact, Field(discriminator="kind")]


def fact_type(fact: CanonicalFact) -> str:
    """Name the type that processing selections match for one fact.

    Returns:
        The core payload kind, or the extension event type.

    """
    return fact.payload.kind if isinstance(fact, CoreFact) else fact.event_type


class CommittedFact(WireModel):
    """Attach host-owned storage metadata to an accepted fact."""

    fact: CanonicalFact
    cursor: Annotated[int, Field(ge=1)]
    accepted_at: float


class CoreStateSnapshot(WireModel):
    """Supply accepted facts at one history boundary.

    Complete means every fact in the request scope at or before after_cursor
    is present. False makes no completeness claim. A bounded or older host
    can supply a subset; absence from that subset does not prove nonexistence.
    """

    after_cursor: Revision
    facts: tuple[CommittedFact, ...] = ()
    complete: bool = False

    @model_validator(mode="after")
    def validate_boundary(self) -> Self:
        """Keep prior facts ordered and inside the selected boundary.

        Returns:
            The checked snapshot.

        Raises:
            ValueError: If a fact cursor is repeated, out of order, or too new.

        """
        cursors = tuple(stored.cursor for stored in self.facts)
        if tuple(sorted(set(cursors))) != cursors:
            message = "prior fact cursors must be unique and increasing"
            raise ValueError(message)
        if cursors and cursors[-1] > self.after_cursor:
            message = "prior fact cursor exceeds the snapshot boundary"
            raise ValueError(message)
        return self
