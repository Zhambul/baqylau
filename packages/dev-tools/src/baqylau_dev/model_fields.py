# Copyright (c) 2026 Zhambyl Yermagambet
"""Treat the fields of a package's pydantic documents as data contract entries.

The host and its clients read a document's fields through JSON, so the package's
own code may never read them. Only fields that a class declares, of classes
that inherit pydantic's `BaseModel`, are entries; other unused code still fails.
"""

import ast

from baqylau_dev.class_declarations import CodeLocation, class_contracts
from baqylau_dev.source_inventory import SourceModule

MODEL_BASES = frozenset(("pydantic.BaseModel", "pydantic.main.BaseModel"))
MODEL_CONFIG = "model_config"


def model_field_entries(modules: tuple[SourceModule, ...]) -> frozenset[CodeLocation]:
    """Find every declared field of every document class.

    Returns:
        The field declarations.

    """
    contracts = class_contracts(modules)
    documents = frozenset(contract.name for contract in contracts if contract.bases & MODEL_BASES)
    return frozenset(location for module in modules for location in _module_fields(module, documents))


def _module_fields(module: SourceModule, documents: frozenset[str]) -> tuple[CodeLocation, ...]:
    nodes = ast.walk(module.tree)
    classes = (node for node in nodes if isinstance(node, ast.ClassDef))
    return tuple(
        location
        for node in classes
        if f"{module.name}.{node.name}" in documents
        for location in _fields(module, node)
    )


def _fields(module: SourceModule, node: ast.ClassDef) -> tuple[CodeLocation, ...]:
    return tuple(
        CodeLocation(path=module.path, line=member.lineno, name=name)
        for member in node.body
        for name in _declared_names(member)
    )


def _declared_names(member: ast.stmt) -> tuple[str, ...]:
    if isinstance(member, ast.AnnAssign) and isinstance(member.target, ast.Name):
        return (member.target.id,)
    if isinstance(member, ast.Assign):
        names = (target.id for target in member.targets if isinstance(target, ast.Name))
        return tuple(name for name in names if name == MODEL_CONFIG)
    return ()
