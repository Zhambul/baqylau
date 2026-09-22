# Copyright (c) 2026 Zhambyl Yermagambet
"""Check public schema export and all closed enum values."""

from enum import StrEnum
from typing import get_args

import pytest
from baqylau_extension_api.core import states
from baqylau_extension_api.core.payloads import CorePayload
from baqylau_extension_api.core.registry import CORE_SCHEMA_VERSION
from baqylau_extension_api.models.documents import EncodedDocument, SchemaIdentity
from baqylau_extension_api.schema_documents import export_schema
from baqylau_extension_api.schemas import SchemaSet
from pydantic import TypeAdapter

from domain import messaging, outcomes, usage, work_state
from tests.extension_api import core_samples

ENUM_TYPES = (
    outcomes.Outcome, outcomes.ExecutionMode, outcomes.FileAction, outcomes.PlanState, outcomes.WorktreeAction,
    outcomes.ProgressStream, outcomes.OutputMode, work_state.ModelChangeReason, work_state.EffortChangeReason,
    work_state.TitleOrigin, work_state.GoalState, work_state.ShellFollowUntil, work_state.TaskState,
    messaging.ActorRole, messaging.MessageRole, messaging.MessagePhase, usage.UsageScope,
)
CORE_ADAPTER: TypeAdapter[CorePayload] = TypeAdapter(CorePayload)


@pytest.mark.parametrize("enum_type", ENUM_TYPES)
def test_public_states_match_private_values(enum_type: type[StrEnum]) -> None:
    """Reject missing or extra public enum values without random sampling."""
    public_values = set(get_args(getattr(states, enum_type.__name__)))
    assert public_values == {member.value for member in enum_type}


def test_exported_schema_validates_every_payload() -> None:
    """Validate every event fixture from exported schema text alone."""
    identity = SchemaIdentity(owner="baqylau", name="core.payload", version=CORE_SCHEMA_VERSION)
    definition = export_schema(CORE_ADAPTER, identity)
    schemas = SchemaSet((definition,))
    for payload in core_samples.PAYLOADS:
        schemas.validate(EncodedDocument(schema_ref=definition.reference, json_text=payload.model_dump_json()))
    assert definition == export_schema(CORE_ADAPTER, identity)
