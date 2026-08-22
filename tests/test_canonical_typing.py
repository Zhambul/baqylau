"""The type gate's own guardrails.

Two gates enforce static typing and they check different halves of it: ruff's
ANN rules say every def carries an annotation, mypy says the annotation is true.
Both are ratcheted the same way — a per-package exemption for code whose
migration has not landed — and the exemptions are the only thing standing
between this repo and "all of it is typed".

That makes the exemption lists the thing to guard. An exemption can rot in two
directions and both are silent:

  * the two lists DRIFT, and a package ruff still checks is one mypy no longer
    does (or the reverse), so unannotated code lands through the gap;
  * an exemption OUTLIVES its migration, and goes on protecting whatever is
    written in that package next.

These tests fail on either. Nothing here checks the code — mypy does that, in
`make typecheck`. This checks the gate.

A third gate, below, checks something ruff and mypy both let through: an
annotation that is TRUE but LOOSE. `dict[str, Any]` satisfies both gates and
tells the reader nothing. That gate is AST-based, not a config check, so it
finds the loose spot itself instead of trusting a per-package exemption list.
"""

from __future__ import annotations

import ast
import configparser
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import tomllib

import pytest


ROOT = pathlib.Path(__file__).resolve().parent.parent


def _mypy_exempt_packages() -> set[str]:
    """The packages mypy.ini stops requiring annotations from."""
    parser = configparser.ConfigParser()
    parser.read(ROOT / "mypy.ini", encoding="utf-8")
    exempt = set()
    for section in parser.sections():
        if not section.startswith("mypy-"):
            continue
        if parser.get(section, "disallow_untyped_defs", fallback="True") != "False":
            continue
        for pattern in section[len("mypy-"):].split(","):
            package = pattern.strip().removesuffix(".*")
            if package:
                exempt.add(package)
    return exempt


def _ruff_exempt_packages() -> set[str]:
    """The packages ruff.toml stops applying the ANN rules to."""
    config = tomllib.loads((ROOT / "ruff.toml").read_text(encoding="utf-8"))
    per_file = config["lint"]["per-file-ignores"]
    exempt = set()
    for glob, codes in per_file.items():
        if not any(code == "ANN" or code.startswith("ANN") for code in codes):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_]+)/\*\*/\*\.py", glob)
        if match:
            exempt.add(match.group(1))
    return exempt


def test_the_two_type_gates_exempt_exactly_the_same_packages():
    """ruff and mypy must ratchet in lockstep.

    They answer different questions, so a package exempted from one and not the
    other is not "half checked" — it is a hole shaped like whichever question
    the remaining gate does not ask. Migrating a package means deleting it from
    BOTH lists, in the same commit, and this is what says so.
    """
    assert _mypy_exempt_packages() == _ruff_exempt_packages()


# pytest.ini's 30s default is meant for the hermetic e2e tests. This one spawns a
# full `mypy --no-incremental` PER exempt package — about 25s of subprocess on a
# warm machine, and more under `-n auto` contention — so it was passing on a
# margin of under a second and failing as a timeout whenever the tree grew.
@pytest.mark.timeout(180)
def test_no_type_exemption_outlives_its_migration():
    """An exemption that is no longer load-bearing must be deleted, not left.

    This is the rule the whole ratchet rests on: exemptions only ever shrink. A
    package that would pass WITHOUT its mypy.ini block is already migrated, and
    leaving the block behind would silently re-open it for the next thing
    written there — which is exactly the failure the gate exists to prevent.

    Checked by re-running mypy against a config with that one package's block
    DELETED, which is exactly what its migration commit will do. (A
    command-line --disallow-untyped-defs would not do: per-module settings win
    over the flag, so the check would pass vacuously for every package.)
    """
    exempt = sorted(_mypy_exempt_packages())
    still_needed = []
    for package in exempt:
        parser = configparser.ConfigParser()
        parser.read(ROOT / "mypy.ini", encoding="utf-8")
        parser.remove_section(f"mypy-{package}.*")
        with tempfile.NamedTemporaryFile(
            "w", suffix=".ini", dir=ROOT, delete=False, encoding="utf-8"
        ) as handle:
            parser.write(handle)
            config_path = handle.name
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "mypy", "--no-incremental",
                 "--config-file", config_path, package],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
        finally:
            os.unlink(config_path)
        if completed.returncode != 0:
            still_needed.append(package)
    assert exempt == still_needed, (
        "these packages now pass without their mypy.ini exemption — delete the "
        "block (and the matching ruff.toml per-file-ignore)"
    )


def test_a_type_ignore_names_the_error_it_silences():
    """`# type: ignore` with no code silences everything, including the next bug.

    ruff's PGH003 enforces this and is the real gate; this test states the rule
    in the suite as well, because the shape it forbids is the one that would
    otherwise accumulate quietly under a green checkmark.
    """
    blanket = []
    for path in ROOT.rglob("*.py"):
        if any(part in {"__pycache__", ".claude", ".venv"} for part in path.parts):
            continue
        if path.resolve() == pathlib.Path(__file__).resolve():
            continue   # this file NAMES the shape it forbids
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"#\s*type:\s*ignore(?!\[)", line):
                blanket.append(f"{path.relative_to(ROOT)}:{number}")
    assert blanket == []



# --- Gate: no loose annotation --------------------------------------------
#
# `Any` and `object` both say "could be anything" — the type checker stops
# helping the moment one appears, no matter how deep it is nested. This gate
# bans them from every annotation (parameter, return, class field, variable)
# in the production packages, together with the container shapes that hide
# the same hole one level down: `dict[str, Any]`, `list[Any]`, a bare `dict`
# with no argument at all. `tuple[Any, ...]` trips the same way a subscript
# does; a bare `tuple` does not, because a fixed-length tuple with unstated
# element types is a narrower, less common shape than the other three.
#
# This was wave 1 of the owner's "absolute type safety" decision: it PINNED
# the loose spots of the day in LOOSE_ANNOTATION_ALLOWED without redesigning
# the parsing code that produced most of them. Wave 2 was that redesign, and
# it emptied the list. It stays empty: an entry may return only with a
# justified reason, the same as the day it was seeded.
#
# An entry needs two things, not one: the allowlist entry below, AND a
# `# loose: <reason>` comment on the offending line. Either alone is not
# enough — the comment without the list entry does not silence the gate,
# and the list entry without the comment lets the line drift silently once
# something else on it changes. Both together mean a reader sees WHY the
# instant they look at the line, and the list stays the single place that
# says how many such spots remain.
LOOSE_ANNOTATION_PACKAGES = (
    "api",
    "app",
    "audit",
    "core",
    "dashboard",
    "domain",
    "engine",
    "harness",
    "notify",
    "repository",
    "terminal",
)

LOOSE_ANNOTATION_ALLOWED: frozenset[str] = frozenset()


def _loose_annotation_paths() -> list[pathlib.Path]:
    paths = []
    for package in LOOSE_ANNOTATION_PACKAGES:
        for path in sorted((ROOT / package).rglob("*.py")):
            if "__pycache__" not in path.parts:
                paths.append(path)
    return paths


def _loose_container_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_any_or_object(node: ast.expr) -> bool:
    return _loose_container_name(node) in ("Any", "object")


def _is_bare_loose_container(node: ast.expr) -> bool:
    return _loose_container_name(node) in ("dict", "list", "set")


def _contains_any_or_object(node: ast.expr | None) -> bool:
    """True if a dict/list/tuple/set subscript carries `Any` or `object`.

    Checked one level of nesting at a time, so `dict[str, list[Any]]` trips
    through the recursive call on its second argument.
    """
    if node is None:
        return False
    if _is_any_or_object(node):
        return True
    if isinstance(node, ast.Subscript):
        if _loose_container_name(node.value) not in ("dict", "list", "tuple", "set"):
            return False
        arguments = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
        return any(_contains_any_or_object(argument) for argument in arguments)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _contains_any_or_object(node.left) or _contains_any_or_object(node.right)
    return False


def _loose_annotation(node: ast.expr | None) -> bool:
    """True for `Any`, `object`, a bare `dict`/`list`/`set`, or a subscript

    of `dict`/`list`/`tuple`/`set` that carries `Any` or `object` anywhere
    inside it. A typed generic like `dict[str, int]` does not trip.
    """
    if node is None:
        return False
    if _is_any_or_object(node):
        return True
    if _is_bare_loose_container(node):
        return True
    if isinstance(node, ast.Subscript):
        return _contains_any_or_object(node)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _loose_annotation(node.left) or _loose_annotation(node.right)
    return False


class _LooseAnnotationVisitor(ast.NodeVisitor):
    """Walks one module, naming every annotation by its enclosing scope.

    The name is `Outer.Inner.field` — class and function names joined by the
    scope they nest in — chosen instead of a line number because a line
    shifts on every unrelated edit above it, while a name only changes when
    the thing itself is renamed or moved.
    """

    def __init__(self) -> None:
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
            if argument.arg in ("self", "cls"):
                continue
            self._record(argument.annotation, self._qualify(f"{node.name}.{argument.arg}"))
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
        self.generic_visit(node)

    def _record(self, annotation: ast.expr | None, key: str) -> None:
        if annotation is not None and _loose_annotation(annotation):
            self.violations.append((annotation.lineno, key, ast.unparse(annotation)))


def test_no_loose_annotation_outside_the_seeded_allowlist():
    """`Any`, `object`, and the container shapes that hide the same hole.

    A hit here means: declare the real shape, or (rarely) add a justified
    allowlist entry.
    """
    violations = []
    for path in _loose_annotation_paths():
        relative = str(path.relative_to(ROOT))
        lines = path.read_text(encoding="utf-8").splitlines()
        visitor = _LooseAnnotationVisitor()
        visitor.visit(ast.parse("\n".join(lines)))
        for lineno, key, annotation_text in visitor.violations:
            allowed = f"{relative}:{key}" in LOOSE_ANNOTATION_ALLOWED
            marked = "# loose:" in lines[lineno - 1]
            if allowed and marked:
                continue
            violations.append(
                f"{relative}:{lineno} {key}: {annotation_text} — declare the real "
                f"shape, or (rarely) add a justified allowlist entry"
            )
    assert violations == []


def test_the_loose_annotation_allowlist_has_no_dead_entries():
    """The allowlist only ever shrinks — an entry with nothing to protect

    must be deleted, or it silently exempts whatever is written at that
    name next.
    """
    live_keys = set()
    for path in _loose_annotation_paths():
        relative = str(path.relative_to(ROOT))
        lines = path.read_text(encoding="utf-8").splitlines()
        visitor = _LooseAnnotationVisitor()
        visitor.visit(ast.parse("\n".join(lines)))
        for lineno, key, _ in visitor.violations:
            if "# loose:" in lines[lineno - 1]:
                live_keys.add(f"{relative}:{key}")
    dead = LOOSE_ANNOTATION_ALLOWED - live_keys
    assert dead == set(), f"remove these dead allowlist entries: {sorted(dead)}"
