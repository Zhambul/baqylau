# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep extension source behind its public SDK and declared dependencies."""

import ast
import sys
import tomllib
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from pydantic import BaseModel

from baqylau_dev.import_names import import_prefix
from baqylau_dev.source_inventory import SourceModule

HOST_ROOTS = frozenset((
    "app", "api", "audit", "cli", "client", "core", "dashboard", "domain", "engine",
    "extensions", "harness", "notify", "repository", "terminal",
))
STORAGE_ROOTS = frozenset(("sqlite3", "aiosqlite", "apsw", "sqlalchemy"))


class ProjectDependencies(BaseModel):
    """Read standard project dependencies without accepting a rule allowlist."""

    dependencies: tuple[str, ...] = ()


def check_imports(root: Path, modules: tuple[SourceModule, ...]) -> None:
    """Reject private host access, storage clients, and undeclared packages."""
    allowed = (
        sys.stdlib_module_names | _dependency_roots(root)
        | {module.name.split(".")[0] for module in modules} | {"baqylau_extension_api"}
    )
    for module in modules:
        for node in ast.walk(module.tree):
            for name in node_imports(module, node):
                _require_import(module, name, allowed)


def _dependency_roots(root: Path) -> frozenset[str]:
    document = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    project = ProjectDependencies.model_validate(document.get("project", {}))
    declared = {canonicalize_name(Requirement(requirement).name) for requirement in project.dependencies}
    return frozenset(
        module for module, distributions in metadata.packages_distributions().items()
        if any(canonicalize_name(distribution) in declared for distribution in distributions)
    )


def node_imports(module: SourceModule, node: ast.AST) -> tuple[str, ...]:
    """Name the modules and members that one import node imports.

    Returns:
        The imported names; no names for other nodes.

    """
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    if isinstance(node, ast.ImportFrom):
        prefix = import_prefix(module, node)
        return (prefix, *(f"{prefix}.{alias.name}" for alias in node.names))
    return ()


def _require_import(module: SourceModule, name: str, allowed: frozenset[str]) -> None:
    prefix = name.split(".", 1)[0]
    if prefix in HOST_ROOTS or name.startswith("baqylau_extension_api.runtime"):
        message = f"{module.name} imports private host code: {name}"
        raise ValueError(message)
    if prefix in STORAGE_ROOTS:
        message = f"{module.name} imports direct storage access: {name}; use host record services"
        raise ValueError(message)
    if prefix not in allowed:
        message = f"{module.name} imports an undeclared dependency: {name}"
        raise ValueError(message)
