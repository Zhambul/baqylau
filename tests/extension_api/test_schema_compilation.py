# Copyright (c) 2026 Zhambyl Yermagambet
"""Compile one schema set once, because the host builds it on each processing step."""

from dataclasses import dataclass, field

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.documents import EncodedDocument
from baqylau_extension_api.schemas import SchemaSet
from jsonschema import Draft202012Validator

from tests.extension_api import samples as fixtures

CHECK_SCHEMA = Draft202012Validator.check_schema


@dataclass
class _CountedChecks:
    """Count the schema checks, and keep each one."""

    count: int = field(default=0)

    def check(self, document: object) -> None:
        """Count one check and run it."""
        self.count += 1
        CHECK_SCHEMA(document)  # type: ignore[arg-type]


def test_equal_definitions_compile_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second set of the same definitions checks no schema again, and still validates."""
    checks = _CountedChecks()
    monkeypatch.setattr(Draft202012Validator, "check_schema", checks.check)
    definition = fixtures.schema_definition(name="compiled-once")

    SchemaSet((definition,))
    compiled_checks = checks.count
    SchemaSet((definition,)).validate(EncodedDocument(schema_ref=definition.reference, json_text='"hello"'))

    assert compiled_checks > 0
    assert checks.count == compiled_checks


def test_a_refused_set_is_refused_each_time() -> None:
    """A refused schema set is not kept, so each build reports the fault again."""
    refused = (fixtures.schema_definition(), fixtures.schema_definition('{"type":"integer"}'))
    for _ in range(2):
        with pytest.raises(ExtensionContractError, match="unique"):
            SchemaSet(refused)
