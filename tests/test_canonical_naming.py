"""Two naming gates, enforced by AST inspection.

Gate 1 — a parameter is named after its class. A parameter whose annotation is
one of OUR classes must carry that class's full name in snake case, either
exactly (`session_repository: SessionRepository`) or as a suffix
(`resume_session_id: SessionId`). A shortened name (`sessions:
SessionRepository`) hides what the object is, and the reader has to open the
class to find out.

Gate 2 — an id is a typed id, never a bare `str`. A parameter or a dataclass
field whose name ends in `_id` must use a NewType from `domain/ids.py` (or a
package's own id type). A bare `str` lets any string flow into any id slot,
and the type checker cannot catch the swap.

Scope: the production packages. `tests/` is not swept (same ratchet stance as
mypy.ini). `api/` is exempt from Gate 2 only: the wire is strings by design,
and its mappers are exactly where typed ids become strings.
"""

from __future__ import annotations

import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent

PACKAGES = (
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

# Gate 2 does not apply to api/: the wire carries strings, and the api mappers
# are the one sanctioned place where a typed id is turned into one.
ID_GATE_PACKAGES = tuple(package for package in PACKAGES if package != "api")

# Parameters that hold an id of NO fixed kind. Each line is a deliberate,
# justified exception; this list only ever shrinks.
ID_GATE_ALLOWED = {
    # stable_event_id builds identity from whatever subject an event has —
    # a shell id, a call id, a path. There is no one kind to name.
    "domain/ids.py:subject_id",
}


def _module_paths() -> list[pathlib.Path]:
    paths = []
    for package in PACKAGES:
        for path in sorted((ROOT / package).rglob("*.py")):
            if "__pycache__" not in path.parts:
                paths.append(path)
    return paths


def _snake(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", name).lower()


def _project_classes() -> set[str]:
    classes = set()
    for path in _module_paths():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                classes.add(node.name)
    return classes


def _simple_annotation_name(annotation: ast.expr | None) -> str | None:
    """`X` or `X | None` gives "X"; anything else gives None.

    Unions of two real classes, generics and subscripts carry no single class
    to name a parameter after, so the gates skip them.
    """
    if isinstance(annotation, ast.Name):
        return annotation.id
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        sides = [_simple_annotation_name(side) for side in (annotation.left, annotation.right)]
        real = [side for side in sides if side is not None and side != "None"]
        return real[0] if len(real) == 1 else None
    if isinstance(annotation, ast.Constant) and annotation.value is None:
        return "None"
    return None


def _annotated_parameters(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for argument in (
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            ):
                if argument.arg in ("self", "cls"):
                    continue
                yield node, argument


def test_a_parameter_is_named_after_its_class():
    classes = _project_classes()
    violations = []
    for path in _module_paths():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function, argument in _annotated_parameters(tree):
            class_name = _simple_annotation_name(argument.annotation)
            if class_name not in classes:
                continue
            wanted = _snake(class_name)
            if argument.arg == wanted or argument.arg.endswith(f"_{wanted}"):
                continue
            violations.append(
                f"{path.relative_to(ROOT)}:{function.lineno} "
                f"{function.name}({argument.arg}: {class_name}) — name it "
                f"`{wanted}` or `<qualifier>_{wanted}`"
            )
    assert violations == []


def _is_bare_str(annotation: ast.expr | None) -> bool:
    if isinstance(annotation, ast.Name):
        return annotation.id == "str"
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        return _is_bare_str(annotation.left) or _is_bare_str(annotation.right)
    return False


def test_an_id_is_a_typed_id_not_a_bare_str():
    violations = []
    for path in _module_paths():
        if path.relative_to(ROOT).parts[0] not in ID_GATE_PACKAGES:
            continue
        relative = str(path.relative_to(ROOT))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function, argument in _annotated_parameters(tree):
            if not argument.arg.endswith("_id"):
                continue
            if f"{relative}:{argument.arg}" in ID_GATE_ALLOWED:
                continue
            if _is_bare_str(argument.annotation):
                violations.append(
                    f"{relative}:{function.lineno} "
                    f"{function.name}({argument.arg}: str) — use a NewType "
                    f"from domain/ids.py"
                )
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for statement in node.body:
                if not isinstance(statement, ast.AnnAssign):
                    continue
                if not isinstance(statement.target, ast.Name):
                    continue
                field_name = statement.target.id
                if not field_name.endswith("_id"):
                    continue
                if f"{relative}:{field_name}" in ID_GATE_ALLOWED:
                    continue
                if _is_bare_str(statement.annotation):
                    violations.append(
                        f"{relative}:{statement.lineno} "
                        f"{node.name}.{field_name}: str — use a NewType "
                        f"from domain/ids.py"
                    )
    assert violations == []
