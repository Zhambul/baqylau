# Copyright (c) 2026 Zhambyl Yermagambet
"""Read protocol shapes from the installed public SDK, not a second table."""

import ast
import inspect
from dataclasses import dataclass
from types import NoneType
from typing import get_args, get_type_hints

from baqylau_extension_api.contracts.plugin import ExtensionCapabilities, ExtensionPlugin

from baqylau_dev.signatures import MethodSignatures, method_signatures

FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass(frozen=True)
class ProtocolSpec:
    """Describe an installed SDK protocol and its manifest capability."""

    name: str
    capability: str | None
    members: MethodSignatures


def sdk_protocols() -> tuple[ProtocolSpec, ...]:
    """Read supported capability protocols from the SDK composition class.

    Returns:
        The main protocol and each implemented optional capability protocol.

    """
    return (
        _protocol_spec(ExtensionPlugin, None),
        *(
            _protocol_spec(candidate, capability)
            for capability, annotation in get_type_hints(ExtensionCapabilities).items()
            for candidate in get_args(annotation) or (annotation,)
            if isinstance(candidate, type) and candidate is not NoneType
        ),
    )


def _protocol_spec(protocol: type, capability: str | None) -> ProtocolSpec:
    tree = ast.parse(inspect.getsource(protocol))
    declaration = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    name = f"{protocol.__module__}.{protocol.__name__}"
    return ProtocolSpec(name=name, capability=capability, members=_required_signatures(declaration))


def required_methods(declaration: ast.ClassDef) -> frozenset[str]:
    """Name the protocol methods that an implementation must define.

    A method with a body is a default method: the implementation inherits it
    from its explicit protocol base, so it is not required.

    Returns:
        The names of the stub methods.

    """
    methods = [member for member in declaration.body if isinstance(member, FUNCTIONS)]
    stubs = [method for method in methods if all(map(_empty, method.body))]
    return frozenset(method.name for method in stubs)


def _required_signatures(declaration: ast.ClassDef) -> MethodSignatures:
    required = required_methods(declaration)
    signatures = method_signatures(declaration)
    return {name: signatures[name] for name in sorted(required)}


def _empty(statement: ast.stmt) -> bool:
    if isinstance(statement, ast.Pass):
        return True
    constant = statement.value if isinstance(statement, ast.Expr) else None
    return isinstance(constant, ast.Constant) and _placeholder(constant.value)


def _placeholder(literal: object) -> bool:
    return literal is Ellipsis or isinstance(literal, str)
