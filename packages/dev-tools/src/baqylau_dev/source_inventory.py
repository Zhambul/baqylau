# Copyright (c) 2026 Zhambyl Yermagambet
"""Read an explicit Python source inventory without importing feature modules."""

import ast
from dataclasses import dataclass
from pathlib import Path

from baqylau_dev.models import ProjectProfile
from baqylau_dev.profiles import require_local_path


@dataclass(frozen=True)
class SourceModule:
    """Pair a source file with its import name and parsed declarations."""

    path: Path
    name: str
    tree: ast.Module


def source_inventory(root: Path, profile: ProjectProfile) -> tuple[SourceModule, ...]:
    """Resolve declared product modules and reject ambiguous or hidden source.

    Returns:
        Product modules with unique import names and local resolved paths.

    Raises:
        ValueError: If module names overlap or product source is not selected.

    """
    modules = tuple(
        module for name in profile.source_roots
        for module in modules_under(root, require_local_path(root, name))
    )
    if len({module.name for module in modules}) != len(modules):
        message = "source roots contain duplicate Python module names"
        raise ValueError(message)
    _require_complete_inventory(root, profile, modules)
    return modules


def modules_under(root: Path, selected: Path) -> tuple[SourceModule, ...]:
    """Map one source root to files with stable Python import names.

    Returns:
        Parsed source modules, sorted by path.

    """
    paths: tuple[Path, ...] = (selected,)
    if selected.is_dir():
        paths = tuple(sorted(selected.rglob("*.py")))
    base = selected if selected.is_dir() else selected.parent
    if (base / "__init__.py").is_file():
        base = base.parent
    return tuple(_source_module(root, base, path) for path in paths)


def _source_module(root: Path, base: Path, path: Path) -> SourceModule:
    checked = require_local_path(root, path.relative_to(root).as_posix())
    parts = path.relative_to(base).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts or not all(part.isidentifier() for part in parts):
        message = f"invalid Python module path: {path.relative_to(root)}"
        raise ValueError(message)
    return SourceModule(
        path=checked, name=".".join(parts),
        tree=ast.parse(checked.read_text(encoding="utf-8")),
    )


def _require_complete_inventory(root: Path, profile: ProjectProfile, modules: tuple[SourceModule, ...]) -> None:
    selected = {module.path for module in modules}
    tests = tuple(require_local_path(root, name) for name in profile.test_roots)
    for path in root.rglob("*.py"):
        if any(part in profile.design_excludes for part in path.relative_to(root).parts):
            continue
        checked = require_local_path(root, path.relative_to(root).as_posix())
        if checked not in selected and not any(
            checked.is_relative_to(test) for test in tests
        ):
            message = f"Python source is outside the declared roots: {path.relative_to(root)}"
            raise ValueError(message)
