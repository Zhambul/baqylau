# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep projection operations stable and all-or-nothing before storage."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.transforms import Drop, Keep
from pydantic import ValidationError

from tests.extension_api import projection_transform_checks as checks, projection_transform_samples as fixtures

CORE_ENTRY_ID = "core-entry"


def test_empty_projection_transform_keeps_changes() -> None:
    """An omitted decision keeps each existing proposal in input order."""
    request = fixtures.request()
    assert checks.apply(request) == request.changes
    assert checks.apply(request, Keep(input_id=CORE_ENTRY_ID)) == request.changes


def test_insert_survives_dropped_projection() -> None:
    """Add owned feed rows before and after a hidden core entry in stable order."""
    request = fixtures.request().model_copy(update={"changes": (fixtures.core_entry(),)})
    first = checks.extension_addition(fixtures.core_entry(), "first").model_copy(update={
        "position": "before",
    })
    last = checks.extension_addition(fixtures.core_entry(), "last")
    output = checks.apply(request, last, Drop(input_id=CORE_ENTRY_ID, reason="Use extension rows"), first)
    assert output == (first.document, last.document)
    assert request.changes == (fixtures.core_entry(),)


def test_projection_core_feed_insert_is_typed() -> None:
    """Add a second core feed row with a distinct stable row identity."""
    original = fixtures.core_entry()
    request = fixtures.request().model_copy(update={"changes": (original,)})
    addition = checks.core_addition(original)
    assert checks.apply(request, addition) == (original, addition.document)


def test_projection_rejects_unknown_anchor() -> None:
    """Reject a complete batch when even one operation names an unknown change."""
    request = fixtures.request()
    before = request.model_dump_json()
    with pytest.raises(ExtensionContractError, match="unknown input change"):
        checks.apply(
            request, Drop(input_id=CORE_ENTRY_ID, reason="Hide"), Keep(input_id="missing"),
        )
    assert request.model_dump_json() == before


def test_projection_rejects_competing_decisions() -> None:
    """Do not choose between conflicting keep and drop operations by response order."""
    with pytest.raises(ValidationError, match="only one"):
        checks.apply(
            fixtures.request(), Keep(input_id=CORE_ENTRY_ID), Drop(input_id=CORE_ENTRY_ID, reason="Hide"),
        )


def test_projection_rejects_unstable_insert_id() -> None:
    """Recompute a generated operation identity before any output is accepted."""
    addition = checks.extension_addition(fixtures.core_entry())
    changed = addition.model_copy(update={"document": addition.document.model_copy(update={
        "change_id": "forged",
    })})
    with pytest.raises(ExtensionContractError, match="derived identity"):
        checks.apply(fixtures.request(), changed)


def test_projection_additions_cannot_be_anchors() -> None:
    """An addition becomes visible only to the next transform stage."""
    addition = checks.extension_addition(fixtures.core_entry())
    with pytest.raises(ExtensionContractError, match="unknown input change"):
        checks.apply(fixtures.request(), addition, Keep(input_id=addition.document.change_id))
