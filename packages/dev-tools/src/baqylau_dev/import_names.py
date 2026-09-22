# Copyright (c) 2026 Zhambyl Yermagambet
"""Resolve static import aliases without loading project code."""

import ast
from importlib.util import resolve_name

from baqylau_dev.source_inventory import SourceModule


def imported_names(module: SourceModule) -> dict[str, str]:
    """Map local aliases to full import names, including relative imports.

    Returns:
        The static import bindings used by class declarations.

    """
    bindings: dict[str, str] = {}
    for node in ast.walk(module.tree):
        bindings.update(_node_bindings(module, node))
    return bindings


def _node_bindings(module: SourceModule, node: ast.AST) -> dict[str, str]:
    if isinstance(node, ast.Import):
        return dict(_import_binding(alias) for alias in node.names)
    if isinstance(node, ast.ImportFrom):
        prefix = import_prefix(module, node)
        return {alias.asname or alias.name: f"{prefix}.{alias.name}" for alias in node.names}
    return {}


def _import_binding(alias: ast.alias) -> tuple[str, str]:
    target = alias.name
    if not alias.asname:
        target = target.split(".", 1)[0]
    return (alias.asname or target, target)


def import_prefix(module: SourceModule, node: ast.ImportFrom) -> str:
    """Resolve a relative import against the source module's package.

    Returns:
        An absolute module name.

    """
    if not node.level:
        return node.module or ""
    package = module.name
    if module.path.name != "__init__.py":
        package = package.rpartition(".")[0]
    return resolve_name("." * node.level + (node.module or ""), package)


def qualified_name(expression: ast.expr, module: SourceModule) -> str:
    """Resolve a named class base through imports or the current module.

    Returns:
        A full name, with generic type arguments removed.

    """
    if isinstance(expression, ast.Subscript):
        return qualified_name(expression.value, module)
    name = ast.unparse(expression)
    first, separator, suffix = name.partition(".")
    resolved = imported_names(module).get(first, f"{module.name}.{first}")
    return f"{resolved}{separator}{suffix}"
