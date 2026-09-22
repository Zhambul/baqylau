# Copyright (c) 2026 Zhambyl Yermagambet
"""Read protocol shapes from the installed public SDK, not a second table."""

import ast
import inspect
from dataclasses import dataclass
from types import NoneType
from typing import get_args, get_type_hints

from baqylau_extension_api.contracts.plugin import ExtensionCapabilities, ExtensionPlugin

from baqylau_dev.signatures import MethodSignatures, method_signatures


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
    return ProtocolSpec(
        name=f"{protocol.__module__}.{protocol.__name__}", capability=capability,
        members=method_signatures(declaration),
    )
