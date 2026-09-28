# Copyright (c) 2026 Zhambyl Yermagambet
"""A protocol method with a body is a default method, which an implementation does not have to define (P02-T04)."""

import ast

from baqylau_dev.protocol_catalog import required_methods, sdk_protocols

PROTOCOL = '''
class Reader(Protocol):
    def read(self) -> str:
        """Read."""
        ...

    def close(self) -> None:
        """Stop."""

    def size(self) -> int:
        """Give a default size."""
        return 0
'''


def test_only_stub_methods_are_required() -> None:
    """A stub with `...` or only a docstring is required; a method with a body is a default."""
    nodes = ast.parse(PROTOCOL).body
    declaration = next(node for node in nodes if isinstance(node, ast.ClassDef))

    assert required_methods(declaration) == frozenset(("read", "close"))


def test_sdk_protocols_keep_their_methods() -> None:
    """The SDK's protocols have no default methods, so every declared method stays required."""
    lifecycle = next(spec for spec in sdk_protocols() if spec.name.endswith(".ExtensionLifecycle"))

    assert set(lifecycle.members) == {"activate", "deactivate"}
