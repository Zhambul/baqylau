# Copyright (c) 2026 Zhambyl Yermagambet
"""Verify every public core payload and envelope against private domain data."""

from dataclasses import fields

import pytest
from baqylau_extension_api.core.payloads import CorePayload
from baqylau_extension_api.core.registry import CORE_MODELS
from baqylau_extension_api.models.canonical import CommittedFact, CoreFact

from domain import events
from extensions.mapper import core_events, core_payloads
from tests.extension_api import core_samples


def test_core_vocabulary_is_complete() -> None:
    """Require one public model and fixture for each private core event type."""
    assert set(CORE_MODELS) == set(events.PAYLOAD_TYPES)
    assert {payload.kind for payload in core_samples.PAYLOADS} == set(CORE_MODELS)
    assert len(core_samples.PAYLOADS) == len(CORE_MODELS)


@pytest.mark.parametrize("event_type", tuple(CORE_MODELS))
def test_core_fields_match_private_fields(event_type: str) -> None:
    """Make any added, removed, or renamed private field fail the contract check."""
    private_fields = {field.name for field in fields(events.PAYLOAD_TYPES[event_type])}
    public_fields = set(CORE_MODELS[event_type].model_fields) - {"kind"}
    assert public_fields == private_fields
    assert CORE_MODELS[event_type].model_fields["kind"].default == event_type


@pytest.mark.parametrize("payload", core_samples.PAYLOADS, ids=lambda payload: payload.kind)
def test_core_payload_round_trip(payload: CorePayload) -> None:
    """Keep all fixture values through private, public, wire, and private forms."""
    original = core_samples.original_payload(payload)
    mapped = core_payloads.public_payload(original)
    wire = core_payloads.PUBLIC_PAYLOAD.validate_json(mapped.model_dump_json())
    assert wire == payload
    assert core_payloads.private_payload(wire) == original


@pytest.mark.parametrize("payload", core_samples.PAYLOADS, ids=lambda payload: payload.kind)
def test_committed_core_envelope_round_trip(payload: CorePayload) -> None:
    """Preserve each identity, source link, and acceptance field."""
    original = core_samples.original_event(core_samples.original_payload(payload))
    wire = CommittedFact.model_validate_json(core_events.public_committed(original).model_dump_json())
    assert isinstance(wire.fact, CoreFact)
    assert core_events.private_committed(wire) == original


def test_candidate_does_not_claim_acceptance() -> None:
    """Discard storage metadata when mapping a candidate for transformation."""
    original = core_samples.original_event(core_samples.original_payload(core_samples.PAYLOADS[0]))
    restored = core_events.private_candidate(core_events.public_candidate(original))
    assert restored.cursor is None and restored.accepted_at is None
