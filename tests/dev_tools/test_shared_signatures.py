# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep one protocol signature rule for the host and external packages."""

import ast

import pytest
from baqylau_dev.signatures import method_signatures, satisfies

from tests import architecture_test_tables

SELF_NAME = "self"


@pytest.mark.parametrize(("arguments", "expected"), [
    ("self, request", (SELF_NAME, "request")),
    ("self, request, /", (SELF_NAME, "request", "/")),
    ("self, *, request", (SELF_NAME, "*request")),
    ("self, *requests", (SELF_NAME, "*requests")),
    ("self, **requests", (SELF_NAME, "**requests")),
])
def test_signature_keeps_argument_kinds(arguments: str, expected: tuple[str, ...]) -> None:
    """Equal argument counts are not enough for a keyword-call contract."""
    tree = ast.parse(f"class Handler:\n    def read({arguments}):\n        pass\n")
    declaration = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    assert method_signatures(declaration) == {"read": expected}
    assert not satisfies({"read": expected}, {"read": (SELF_NAME, "renamed")})


def test_host_uses_the_installed_rule_functions() -> None:
    """The extraction must not leave a second implementation in host tests."""
    assert architecture_test_tables.method_signatures is method_signatures
    assert architecture_test_tables.satisfies is satisfies
