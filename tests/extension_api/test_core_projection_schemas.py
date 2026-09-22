# Copyright (c) 2026 Zhambyl Yermagambet
"""Export complete core projection schemas and compare all closed states."""

from enum import StrEnum
from typing import get_args

import pytest
from baqylau_extension_api.core import derived_states, entry_registry
from baqylau_extension_api.core.aggregate import CoreAggregateState
from baqylau_extension_api.core.entries import CoreEntryBody
from baqylau_extension_api.models.documents import EncodedDocument, SchemaIdentity
from baqylau_extension_api.schema_documents import export_schema
from baqylau_extension_api.schemas import SchemaSet
from pydantic import TypeAdapter

from domain import actor_state, entry_base, lifecycle
from tests.extension_api import core_projection_samples

ENUM_TYPES = (
    actor_state.ActorStatus, entry_base.RunState, entry_base.TurnState, entry_base.FileState, lifecycle.LifecycleState,
)


@pytest.mark.parametrize("enum_type", ENUM_TYPES)
def test_projection_states_match_private_enums(enum_type: type[StrEnum]) -> None:
    """Require every current enum value without random fixture selection."""
    expected = {member.value for member in enum_type}
    assert set(get_args(getattr(derived_states, enum_type.__name__))) == expected


def test_core_feed_schema_covers_every_body() -> None:
    """Validate every fixture from the exported closed schema alone."""
    adapter: TypeAdapter[CoreEntryBody] = TypeAdapter(CoreEntryBody)
    identity = SchemaIdentity(owner="baqylau", name="core.feed", version=entry_registry.CORE_PROJECTION_SCHEMA_VERSION)
    definition = export_schema(adapter, identity)
    schemas = SchemaSet((definition,))
    for body in core_projection_samples.BODIES:
        schemas.validate(EncodedDocument(schema_ref=definition.reference, json_text=body.model_dump_json()))
    assert definition == export_schema(adapter, identity)


def test_core_aggregate_schema_covers_full_state() -> None:
    """Include all nested core rows and internal state in the exported schema."""
    identity = SchemaIdentity(
        owner="baqylau", name="core.aggregate", version=entry_registry.CORE_PROJECTION_SCHEMA_VERSION,
    )
    definition = export_schema(TypeAdapter(CoreAggregateState), identity)
    SchemaSet((definition,)).validate(EncodedDocument(
        schema_ref=definition.reference, json_text=core_projection_samples.AGGREGATE.model_dump_json(),
    ))
