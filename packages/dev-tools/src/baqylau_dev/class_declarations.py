# Copyright (c) 2026 Zhambyl Yermagambet
"""Resolve local class inheritance while keeping protocol stubs abstract."""

import ast
from dataclasses import dataclass
from pathlib import Path

from baqylau_dev.import_names import qualified_name
from baqylau_dev.signatures import MethodSignatures, method_signatures
from baqylau_dev.source_inventory import SourceModule


@dataclass(frozen=True)
class CodeLocation:
    """Identify one declaration, not every use of its name in a project."""

    path: Path
    line: int
    name: str


@dataclass(frozen=True)
class ClassContract:
    """Keep complete concrete signatures and their defining source locations."""

    name: str
    bases: frozenset[str]
    members: MethodSignatures
    locations: dict[str, CodeLocation]
    is_protocol: bool


def class_contracts(modules: tuple[SourceModule, ...]) -> tuple[ClassContract, ...]:
    """Resolve inherited concrete methods across explicit source modules.

    Returns:
        Concrete class contracts, with cycle checks and no inherited SDK stubs.

    """
    declarations = {
        f"{module.name}.{node.name}": (module, node)
        for module in modules for node in ast.walk(module.tree) if isinstance(node, ast.ClassDef)
    }
    return tuple(_resolve_contract(name, declarations, ()) for name in declarations)


def _resolve_contract(
    name: str,
    declarations: dict[str, tuple[SourceModule, ast.ClassDef]],
    trail: tuple[str, ...],
) -> ClassContract:
    if name in trail:
        _raise_cycle((*trail, name))
    module, node = declarations[name]
    parents = tuple(qualified_name(base, module) for base in node.bases)
    inherited = tuple(
        _resolve_contract(parent, declarations, (*trail, name))
        for parent in reversed(parents) if parent in declarations
    )
    return _merged_contract(module, node, parents, inherited)


def _raise_cycle(trail: tuple[str, ...]) -> None:
    cycle = " -> ".join(trail)
    message = f"class inheritance cycle: {cycle}"
    raise ValueError(message)


def _merged_contract(
    module: SourceModule, node: ast.ClassDef,
    parents: tuple[str, ...], inherited: tuple[ClassContract, ...],
) -> ClassContract:
    members = {name: signature for parent in inherited for name, signature in parent.members.items()}
    locations = {name: location for parent in inherited for name, location in parent.locations.items()}
    members.update(method_signatures(node))
    locations.update({
        member.name: CodeLocation(
            path=module.path, line=member.decorator_list[0].lineno if member.decorator_list else member.lineno,
            name=member.name,
        )
        for member in node.body if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
    })
    return ClassContract(
        name=f"{module.name}.{node.name}",
        bases=frozenset((*parents, *(base for parent in inherited for base in parent.bases))),
        members=members, locations=locations, is_protocol="typing.Protocol" in parents,
    )
