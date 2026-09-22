# Copyright (c) 2026 Zhambyl Yermagambet
"""Capture typed core aggregate state without a private repository object."""

from typing import Annotated

from pydantic import Field

from baqylau_extension_api.core.actor_state import CoreActorFacts
from baqylau_extension_api.core.session_state import CoreSessionFacts
from baqylau_extension_api.models.base import WireModel

MAX_CORE_ACTORS = 1000


class CoreAggregateState(WireModel):
    """Supply a session row and its bounded actor rows at one host snapshot."""

    session: CoreSessionFacts | None = None
    actors: Annotated[tuple[CoreActorFacts, ...], Field(max_length=MAX_CORE_ACTORS)] = ()
