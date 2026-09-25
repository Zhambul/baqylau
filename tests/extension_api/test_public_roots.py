# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep the SDK and test kit dead-code roots tied to declared public names."""

import ast
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SDK_ROOT = ROOT / "packages" / "extension-api" / "src" / "baqylau_extension_api"
TESTKIT_ROOT = ROOT / "packages" / "extension-testkit" / "src" / "baqylau_extension_testkit"


def public_members(declaration: ast.ClassDef) -> Iterator[str]:
    """Read public fields and methods declared on one SDK class.

    Yields:
        Fully qualified class member names.

    """
    for member in declaration.body:
        if isinstance(member, ast.FunctionDef):
            yield f"{declaration.name}.{member.name}"
        elif isinstance(member, ast.AnnAssign) and isinstance(member.target, ast.Name):
            field_name = member.target.id
            yield f"{declaration.name}.{field_name}"


def public_names(path: Path) -> Iterator[str]:
    """Read public class, constant, and member names from SDK source.

    Yields:
        Declared names without executing the source file.

    """
    for declaration in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(declaration, ast.ClassDef):
            yield declaration.name
            yield from public_members(declaration)
        elif isinstance(declaration, ast.Assign):
            yield from (target.id for target in declaration.targets if isinstance(target, ast.Name))
        elif isinstance(declaration, ast.FunctionDef):
            yield declaration.name


def test_deadcode_roots_are_real_public_names() -> None:
    """Reject an invented or removed SDK or test kit entry point in the Vulture roots."""
    sources = (*SDK_ROOT.rglob("*.py"), *TESTKIT_ROOT.rglob("*.py"))
    declared = {name for path in sources for name in public_names(path)}
    tree = ast.parse((ROOT / "vulture_extension_api.py").read_text(encoding="utf-8"))
    selected = {
        ast.unparse(node.value)
        for node in tree.body[1:]
        if isinstance(node, ast.Expr)
    }
    assert selected and selected <= declared
    for name in selected:
        assert all(not part.startswith("_") for part in name.split("."))
