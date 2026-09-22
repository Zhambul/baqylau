# Copyright (c) 2026 Zhambyl Yermagambet
"""Check every core feed kind, field, and value against current private data."""

from dataclasses import fields, replace

import pytest
from baqylau_extension_api.core.entries import CoreEntryBody, CoreSessionEntry
from baqylau_extension_api.core.entry_registry import CORE_ENTRY_MODELS

from domain import entries
from extensions.mapper import core_entries, core_entry_bodies
from tests.extension_api import core_projection_samples


def test_public_feed_vocabulary_is_complete() -> None:
    """Require one public model and one full fixture for each stored body kind."""
    assert set(CORE_ENTRY_MODELS) == set(entries.BODY_TYPES)
    assert {body.kind for body in core_projection_samples.BODIES} == set(CORE_ENTRY_MODELS)
    assert len(core_projection_samples.BODIES) == len(CORE_ENTRY_MODELS)


@pytest.mark.parametrize("kind", tuple(CORE_ENTRY_MODELS))
def test_public_feed_fields_match_private(kind: str) -> None:
    """Catch an added or renamed private field before the SDK loses it."""
    private_type = entries.BODY_TYPES[entries.EntryTypeName(kind)]
    private_fields = {field.name for field in fields(private_type)}
    public_fields = set(CORE_ENTRY_MODELS[kind].model_fields) - {"kind"}
    assert public_fields == private_fields
    assert CORE_ENTRY_MODELS[kind].model_fields["kind"].default == kind


@pytest.mark.parametrize("body", core_projection_samples.BODIES, ids=lambda body: body.kind)
def test_core_feed_body_round_trip(body: CoreEntryBody) -> None:
    """Preserve every fixture field through private, public, and wire forms."""
    original = core_projection_samples.original_body(body)
    mapped = core_entry_bodies.public_body(original)
    wire = core_entry_bodies.PUBLIC_ENTRY_BODY.validate_json(mapped.model_dump_json())
    assert wire == body
    assert core_entry_bodies.private_body(wire) == original


@pytest.mark.parametrize("body", core_projection_samples.BODIES, ids=lambda body: body.kind)
def test_core_feed_envelope_round_trip(body: CoreEntryBody) -> None:
    """Keep the full envelope while leaving commit authority with the host."""
    original = core_projection_samples.original_entry(body)
    mapped = core_entries.public_entry(original)
    wire = CoreSessionEntry.model_validate_json(mapped.model_dump_json())
    restored = core_entries.private_entry(wire)
    assert restored.cursor == 0
    assert replace(restored, cursor=original.cursor) == original


def test_core_feed_envelope_fields_are_complete() -> None:
    """Exclude only the host cursor from the public feed proposal."""
    private_fields = {field.name for field in fields(entries.SessionEntry)} - {"cursor"}
    assert set(CoreSessionEntry.model_fields) == private_fields
