# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep dynamically serialized HTTP enums equal to their host vocabulary."""

from enum import StrEnum
from typing import get_args

import pytest
from pydantic import BaseModel, TypeAdapter

from api.extensions.lifecycle_vocabulary import AdmissionStatus, LifecycleKind, RuntimePhase
from extensions.models.lifecycle_operations import LifecycleProposal
from extensions.models.lifecycle_state import LifecycleAdmission
from extensions.models.manager import ManagerSnapshot


@pytest.mark.parametrize(("enum_type", "host_model", "field"), [
    (RuntimePhase, ManagerSnapshot, "phase"), (LifecycleKind, LifecycleProposal, "kind"),
])
def test_public_enum_matches_host_values(
    enum_type: type[StrEnum], host_model: type[BaseModel], field: str,
) -> None:
    """Every host value maps to one API member and is present in generated schema."""
    host_values = get_args(host_model.model_fields[field].annotation)
    assert set(enum_type) == set(host_values)
    adapter = TypeAdapter(enum_type)
    assert set(adapter.json_schema()["enum"]) == set(host_values)
    for member in enum_type:
        assert adapter.validate_json(adapter.dump_json(member)) == member


def test_admission_enum_excludes_http_refusals() -> None:
    """Stale and busy requests use HTTP errors, not a successful admission body."""
    host_values = get_args(LifecycleAdmission.model_fields["status"].annotation)
    assert set(AdmissionStatus) == set(host_values) - {"stale", "busy"}
