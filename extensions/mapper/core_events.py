# Copyright (c) 2026 Zhambyl Yermagambet
"""Map core envelopes while keeping acceptance metadata host-owned."""

from dataclasses import replace

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.canonical import CommittedFact, CoreFact
from baqylau_extension_api.models.scopes import SessionScope

from domain import ids
from domain.event_base import CanonicalEvent, EventPayload
from extensions.mapper.core_payloads import private_payload, public_payload


def public_candidate(event: CanonicalEvent[EventPayload]) -> CoreFact:
    """Map a private event without accepting it on behalf of the host.

    Returns:
        A typed candidate with its original source and identity fields.

    """
    return CoreFact(
        event_id=event.event_id,
        scope=SessionScope(session_id=event.session_id, actor_id=event.actor_id, harness=event.harness),
        payload=public_payload(event.payload),
        turn_id=event.turn_id,
        parent_actor_id=event.parent_actor_id,
        occurred_at=event.occurred_at,
        terminal_window_id=event.terminal_window_id,
        harness_process_id=event.harness_process_id,
        raw_event_ids=event.raw_event_ids,
    )


def public_committed(event: CanonicalEvent[EventPayload]) -> CommittedFact:
    """Map an accepted event and its exact storage metadata.

    Returns:
        A public committed fact with the original cursor and acceptance time.

    Raises:
        ExtensionContractError: If the event has not been accepted.

    """
    if event.cursor is None or event.accepted_at is None:
        message = "committed core event requires acceptance metadata"
        raise ExtensionContractError(message)
    return CommittedFact(fact=public_candidate(event), cursor=event.cursor, accepted_at=event.accepted_at)


def private_candidate(fact: CoreFact) -> CanonicalEvent[EventPayload]:
    """Restore a candidate without allocating a cursor or acceptance time.

    Returns:
        A private core candidate that the repository must still accept.

    """
    validated = CoreFact.model_validate(fact)
    return CanonicalEvent(
        event_id=ids.CanonicalEventId(validated.event_id),
        session_id=ids.SessionId(validated.scope.session_id),
        actor_id=ids.ActorId(validated.scope.actor_id),
        harness=ids.HarnessName(validated.scope.harness),
        payload=private_payload(validated.payload),
        turn_id=None if validated.turn_id is None else ids.TurnId(validated.turn_id),
        parent_actor_id=None if validated.parent_actor_id is None else ids.ActorId(validated.parent_actor_id),
        occurred_at=validated.occurred_at,
        terminal_window_id=(
            None if validated.terminal_window_id is None else ids.WindowId(validated.terminal_window_id)
        ),
        harness_process_id=validated.harness_process_id,
        raw_event_ids=tuple(ids.RawEventId(source_id) for source_id in validated.raw_event_ids),
    )


def private_committed(stored: CommittedFact) -> CanonicalEvent[EventPayload]:
    """Map an accepted core fact with its actual storage metadata.

    Returns:
        A core event with no invented identity, cursor, or acceptance time.

    Raises:
        ExtensionContractError: If the selected fact is not a core fact.

    """
    if not isinstance(stored.fact, CoreFact):
        message = "an extension fact cannot enter a core reaction"
        raise ExtensionContractError(message)
    return replace(private_candidate(stored.fact), cursor=stored.cursor, accepted_at=stored.accepted_at)
