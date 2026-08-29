"""Reject collection items that use tuples as unnamed records."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PRODUCTION_PACKAGES = tuple(
    sorted(
        {
            path.parent.name
            for path in ROOT.glob("*/__init__.py")
            if path.parent.name != "tests"
        }
        | {"bin", "client"}
    )
)
SEQUENCE_TYPES = frozenset(
    {
        "Collection",
        "Deque",
        "FrozenSet",
        "Generator",
        "Iterable",
        "Iterator",
        "List",
        "Sequence",
        "Set",
        "Tuple",
        "deque",
        "frozenset",
        "list",
        "set",
        "tuple",
    }
)
TUPLE_TYPES = frozenset({"Tuple", "tuple"})

# Each item is an internal algorithm value. It does not cross a public boundary.
# The source line must also have a `raw-record:` reason.
RAW_RECORD_ALLOWED: frozenset[str] = frozenset()


@dataclass(frozen=True)
class _ModuleTypes:
    aliases: dict[str, ast.expr]
    sequence_names: dict[str, str]


def _module_types(tree: ast.Module) -> _ModuleTypes:
    aliases: dict[str, ast.expr] = {}
    sequence_names = {name: name for name in SEQUENCE_TYPES}
    for statement in tree.body:
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            for name in statement.names:
                local_name = name.asname or name.name
                imported_name = name.name.rsplit(".", 1)[-1]
                if imported_name in SEQUENCE_TYPES:
                    sequence_names[local_name] = imported_name
        elif isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target = statement.targets[0]
            if isinstance(target, ast.Name):
                aliases[target.id] = statement.value
        elif isinstance(statement, ast.AnnAssign):
            if isinstance(statement.target, ast.Name) and statement.value is not None:
                aliases[statement.target.id] = statement.value
        elif isinstance(statement, ast.TypeAlias):
            if isinstance(statement.name, ast.Name):
                aliases[statement.name.id] = statement.value
    return _ModuleTypes(aliases, sequence_names)


def _type_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _arguments(node: ast.Subscript) -> tuple[ast.expr, ...]:
    if isinstance(node.slice, ast.Tuple):
        return tuple(node.slice.elts)
    return (node.slice,)


def _resolved(
    node: ast.expr,
    module_types: _ModuleTypes,
    resolving: frozenset[str],
) -> tuple[ast.expr, frozenset[str]]:
    if not isinstance(node, ast.Name):
        return node, resolving
    if node.id in resolving or node.id not in module_types.aliases:
        return node, resolving
    next_resolving = resolving | {node.id}
    return _resolved(module_types.aliases[node.id], module_types, next_resolving)


def _is_fixed_tuple(
    node: ast.expr,
    module_types: _ModuleTypes,
    resolving: frozenset[str],
) -> bool:
    resolved, next_resolving = _resolved(node, module_types, resolving)
    if not isinstance(resolved, ast.Subscript):
        return False
    if _type_name(resolved.value) not in TUPLE_TYPES:
        return False
    arguments = _arguments(resolved)
    return len(arguments) >= 2 and not (
        len(arguments) == 2
        and isinstance(arguments[1], ast.Constant)
        and arguments[1].value is Ellipsis
    )


def _contains_raw_record(
    node: ast.expr | None,
    module_types: _ModuleTypes,
    resolving: frozenset[str] = frozenset(),
) -> bool:
    if node is None:
        return False
    resolved, next_resolving = _resolved(node, module_types, resolving)
    if not isinstance(resolved, ast.Subscript):
        return False
    arguments = _arguments(resolved)
    sequence_name = module_types.sequence_names.get(_type_name(resolved.value) or "")
    if sequence_name in SEQUENCE_TYPES:
        item_arguments = arguments[:1] if sequence_name != "Generator" else arguments[:1]
        if any(
            _is_fixed_tuple(argument, module_types, next_resolving)
            for argument in item_arguments
        ):
            return True
    return any(
        _contains_raw_record(argument, module_types, next_resolving)
        for argument in arguments
    )


class _RawRecordVisitor(ast.NodeVisitor):
    def __init__(self, module_types: _ModuleTypes) -> None:
        self.module_types = module_types
        self.violations: list[tuple[int, str, str]] = []
        self._scope: list[str] = []

    def _qualify(self, name: str) -> str:
        return ".".join((*self._scope, name))

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for argument in (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        ):
            if argument.arg not in ("self", "cls"):
                self._record(
                    argument.annotation,
                    self._qualify(f"{node.name}.{argument.arg}"),
                )
        for optional_argument in (node.args.vararg, node.args.kwarg):
            if optional_argument is not None:
                self._record(
                    optional_argument.annotation,
                    self._qualify(f"{node.name}.{optional_argument.arg}"),
                )
        self._record(node.returns, self._qualify(f"{node.name}.return"))
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name):
            self._record(node.annotation, self._qualify(node.target.id))
            if node.value is not None:
                self._record_alias(node.value, self._qualify(node.target.id))
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        if not self._scope and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                self._record_alias(node.value, target.id)
        self.generic_visit(node)

    def visit_TypeAlias(self, node: ast.TypeAlias) -> None:
        if isinstance(node.name, ast.Name):
            self._record_alias(node.value, node.name.id)
        self.generic_visit(node)

    def _record_alias(self, annotation: ast.expr, key: str) -> None:
        if _is_fixed_tuple(annotation, self.module_types, frozenset()):
            self.violations.append(
                (annotation.lineno, key, ast.unparse(annotation))
            )
            return
        self._record(annotation, key)

    def _record(self, annotation: ast.expr | None, key: str) -> None:
        if annotation is not None and _contains_raw_record(
            annotation, self.module_types
        ):
            self.violations.append(
                (annotation.lineno, key, ast.unparse(annotation))
            )


def _source_violations(source: str) -> list[tuple[int, str, str]]:
    tree = ast.parse(source)
    visitor = _RawRecordVisitor(_module_types(tree))
    visitor.visit(tree)
    return visitor.violations


def test_raw_record_check_resolves_aliases_and_wrapper_forms() -> None:
    source = """
from collections.abc import Iterable, Sequence as Rows
from typing import List, Tuple

Pair = tuple[str, int]
Pairs = Rows[Pair]
LegacyPair = Tuple[str, int]
LegacyPairs = List[LegacyPair]

def bad(values: Iterable[Pair]) -> Pairs:
    return ()

def good(values: tuple[str, ...]) -> list[str]:
    return list(values)
"""
    keys = {key for _line, key, _text in _source_violations(source)}
    assert keys == {
        "LegacyPair",
        "LegacyPairs",
        "Pair",
        "Pairs",
        "bad.return",
        "bad.values",
    }


def test_production_boundaries_do_not_use_raw_tuple_records() -> None:
    violations = []
    for package in PRODUCTION_PACKAGES:
        for path in sorted((ROOT / package).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            relative = str(path.relative_to(ROOT))
            lines = path.read_text(encoding="utf-8").splitlines()
            source_violations = _source_violations("\n".join(lines))
            for line, key, annotation in source_violations:
                allowed = f"{relative}:{key}" in RAW_RECORD_ALLOWED
                marked = "# raw-record:" in lines[line - 1]
                if allowed and marked:
                    continue
                violations.append(
                    f"{relative}:{line} {key}: {annotation} - use a named "
                    "immutable model"
                )
    assert violations == []


def test_raw_record_allowlist_has_no_dead_items() -> None:
    live_items = set()
    for package in PRODUCTION_PACKAGES:
        for path in sorted((ROOT / package).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            relative = str(path.relative_to(ROOT))
            lines = path.read_text(encoding="utf-8").splitlines()
            for line, key, _annotation in _source_violations("\n".join(lines)):
                if "# raw-record:" in lines[line - 1]:
                    live_items.add(f"{relative}:{key}")
    dead_items = RAW_RECORD_ALLOWED - live_items
    assert dead_items == set(), f"remove dead raw record items: {sorted(dead_items)}"
