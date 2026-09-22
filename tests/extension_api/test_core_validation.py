# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject invalid core tags, references, and forged worker data."""

from dataclasses import replace

import pytest
from baqylau_extension_api.core.payloads import CorePayload
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.canonical import CommittedFact, CoreFact
from pydantic import TypeAdapter, ValidationError

from domain.event_base import EventPayload
from extensions.mapper import core_events, core_payloads
from tests.extension_api import core_samples, transform_samples

CORE_ADAPTER: TypeAdapter[CorePayload] = TypeAdapter(CorePayload)


def test_extension_cannot_map_to_core_reaction() -> None:
    """Do not invent a core session for an accepted extension fact."""
    fact = transform_samples.extension_addition(transform_samples.core_fact()).document
    stored = CommittedFact(fact=fact, cursor=1, accepted_at=1000.0)
    with pytest.raises(ExtensionContractError, match="cannot enter a core reaction"):
        core_events.private_committed(stored)


@pytest.mark.parametrize("encoded", [
    '{"kind":"unregistered.type"}',
    '{"kind":"shell.finished","shell_id":"s","outcome":"invented","result":null,"exit_code":0}',
    '{"kind":"actor.started","name":"Actor","role":"child","extra":"not allowed"}',
    '{"kind":"task.list_changed","list_id":"l","task_ids":[1]}',
])
def test_core_union_rejects_invalid_data(encoded: str) -> None:
    """Reject unknown tags, unknown fields, and wrong nested types."""
    with pytest.raises(ValidationError):
        CORE_ADAPTER.validate_json(encoded)


def test_core_rejects_self_parent() -> None:
    """Preserve the current core actor-parent identity invariant."""
    original = core_samples.original_event(core_samples.original_payload(core_samples.PAYLOADS[0]))
    with pytest.raises(ValidationError, match="own parent"):
        core_events.public_candidate(replace(original, parent_actor_id=original.actor_id))


def test_committed_requires_acceptance_metadata() -> None:
    """Do not label a candidate as a stored fact."""
    original = core_samples.original_event(core_samples.original_payload(core_samples.PAYLOADS[0]))
    with pytest.raises(ExtensionContractError, match="acceptance metadata"):
        core_events.public_committed(replace(original, cursor=None))


def test_unknown_private_payload_is_rejected() -> None:
    """Do not allow the private base class to enter the closed public union."""
    with pytest.raises(ExtensionContractError, match="public API"):
        core_payloads.public_payload(EventPayload())


def test_forged_core_instance_is_revalidated() -> None:
    """Reject an unchecked model copy before it reaches private code."""
    original = core_samples.original_event(core_samples.original_payload(core_samples.PAYLOADS[0]))
    forged = core_events.public_candidate(original).model_copy(update={"scope": None})
    with pytest.raises(ValidationError):
        core_events.private_candidate(forged)


def test_core_rejects_worker_acceptance_fields() -> None:
    """Keep cursor and acceptance time out of transform candidate output."""
    original = core_samples.original_event(core_samples.original_payload(core_samples.PAYLOADS[0]))
    encoded = core_events.public_candidate(original).model_dump_json()
    prefix = encoded[:-1]
    with pytest.raises(ValidationError, match="extra_forbidden"):
        CoreFact.model_validate_json(f'{prefix},"cursor":99}}')
