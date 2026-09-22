# Copyright (c) 2026 Zhambyl Yermagambet
"""Map complete core aggregate rows without private SDK imports."""

from baqylau_extension_api.core.actor_state import CoreActorFacts
from baqylau_extension_api.core.session_state import CoreSessionFacts
from pydantic import TypeAdapter

from domain.actor_state import ActorFacts
from domain.session_state import SessionFacts


def public_actor(actor: ActorFacts) -> CoreActorFacts:
    """Read every declared actor field through the public typed model.

    Returns:
        A complete actor row with typed calculation and live-work state.

    """
    return CoreActorFacts.model_validate(actor)


def private_actor(actor: CoreActorFacts) -> ActorFacts:
    """Revalidate an actor row before restoring the private domain type.

    Returns:
        A private actor row; host reference and lifecycle checks remain required.

    """
    checked = CoreActorFacts.model_validate(actor)
    return TypeAdapter(ActorFacts).validate_json(checked.model_dump_json())


def public_session(session: SessionFacts) -> CoreSessionFacts:
    """Read every declared session field through the public typed model.

    Returns:
        A complete session row with typed goal, task, and title state.

    """
    return CoreSessionFacts.model_validate(session)


def private_session(session: CoreSessionFacts) -> SessionFacts:
    """Revalidate a session row before restoring the private domain type.

    Returns:
        A private session row; host reference and lifecycle checks remain required.

    """
    checked = CoreSessionFacts.model_validate(session)
    return TypeAdapter(SessionFacts).validate_json(checked.model_dump_json())
