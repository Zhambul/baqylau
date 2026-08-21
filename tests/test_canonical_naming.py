"""Naming gates, enforced by AST inspection.

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

Gate 4 (below Gate 3, the banned-words gate) — a harness's name is a typed
`HarnessName`, never a bare `str`, for the same reason as Gate 2: a parameter
or dataclass field named exactly `harness` or ending `_harness` must not be
annotated bare `str`.

Scope: the production packages. `tests/` is not swept (same ratchet stance as
mypy.ini). `api/` is exempt from Gates 2 and 4 only: the HTTP boundary carries
strings by design, and its mappers are exactly where a typed id or a typed
harness name becomes one.
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
    # UsageReported.scope says which id `subject_id` is (session, actor, turn
    # or operation) — the same "no one fixed kind" shape as stable_event_id's
    # own subject_id above, and for the same reason.
    "domain/events.py:subject_id",
    "harness/impl/claude_code/canonical/support.py:subject_id",
    "harness/impl/codex/canonical/support.py:subject_id",
    "harness/models/raw_events.py:subject_id",
    # codex's OWN compaction-window id, distinct from domain WindowId (a
    # TERMINAL window) — a naming collision, not the same concept. Unread by
    # any canonical logic (rollout.py module header): carried for a future
    # reader, not worth a NewType for a field nothing consumes yet.
    "harness/impl/codex/canonical/records.py:window_id",
    "harness/impl/codex/canonical/records.py:previous_window_id",
    "harness/impl/codex/canonical/records.py:first_window_id",
    # A hook delivery's own id (SessionStart/PreCompact/PostCompact) — used
    # only as a last-resort native_identity fallback string, the same role
    # raw_event.source_position plays; not a domain concept.
    "harness/impl/codex/canonical/records.py:hook_event_id",
    # The codex CommandExecution item's own id — declared (it is a real
    # field) but never read by any canonical logic, the same as the original
    # dict literal carried it unread. Not worth a NewType for a value with no
    # reader.
    "harness/impl/codex/canonical/records.py:item_id",
    # codex's own account plan-limit identifier (measured: "codex") — unread,
    # opaque, no domain equivalent.
    "harness/impl/codex/canonical/records.py:limit_id",
    # The model provider's own name ("openai") — a vendor label, not an id
    # this codebase has a NewType for.
    "harness/impl/codex/canonical/records.py:model_provider_id",
    # The client UI's own opaque session id on a `user_message` — unread by
    # any canonical logic.
    "harness/impl/codex/canonical/records.py:client_id",
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


# Gate 4 shares Gate 2's exemption: api/ is the HTTP boundary, and its mappers
# are where a typed harness name is turned into a plain string.
HARNESS_GATE_PACKAGES = ID_GATE_PACKAGES

# A parameter or field that holds a harness name but truly cannot be typed.
# Each line is a deliberate, justified exception; this list only ever shrinks.
HARNESS_GATE_ALLOWED: set[str] = set()


def _is_harness_named(name: str) -> bool:
    return name == "harness" or name.endswith("_harness")


def test_a_harness_name_is_a_typed_harness_name_not_a_bare_str():
    violations = []
    for path in _module_paths():
        if path.relative_to(ROOT).parts[0] not in HARNESS_GATE_PACKAGES:
            continue
        relative = str(path.relative_to(ROOT))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function, argument in _annotated_parameters(tree):
            if not _is_harness_named(argument.arg):
                continue
            if f"{relative}:{argument.arg}" in HARNESS_GATE_ALLOWED:
                continue
            if _is_bare_str(argument.annotation):
                violations.append(
                    f"{relative}:{function.lineno} "
                    f"{function.name}({argument.arg}: str) — use HarnessName "
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
                if not _is_harness_named(field_name):
                    continue
                if f"{relative}:{field_name}" in HARNESS_GATE_ALLOWED:
                    continue
                if _is_bare_str(statement.annotation):
                    violations.append(
                        f"{relative}:{statement.lineno} "
                        f"{node.name}.{field_name}: str — use HarnessName "
                        f"from domain/ids.py"
                    )
    assert violations == []


# --- Gate 3: banned words -----------------------------------------------------
#
# A vocabulary the owner has retired, because each word named a thing this tree
# no longer has (a hand-written codec, an "envelope" that is a plain stored
# event, a "wire" that is the HTTP boundary) or was too vague to keep (a
# "wiring" or a "provenance" that always meant something more specific the
# sentence should say instead — a dependency graph, a set of raw event ids,
# whatever the concrete thing actually is). There is no fixed one-word
# replacement for these two: say the plain thing, in place. Grow-only: a word
# retired here may never come back off the list, only new words may join it.
BANNED_WORDS = ("envelope", "evidence", "wire", "wiring", "provenance")

# Classes the owner decided should never exist: one canonical event class,
# `domain.events.CanonicalEvent`, end to end. A second one always meant to grow
# back into a stored-document type this tree no longer has. Grow-only, like
# the word list above.
BANNED_IDENTIFIERS = ("StoredCanonicalEvent", "CommittedEvent", "CanonicalEventDocument")

# Scanned wider than the two naming gates above: `bin/` and `client/` carry
# prose and identifiers too, and the browser half of this codebase is JS, not
# Python.
BANNED_WORD_PACKAGES = PACKAGES + ("bin", "client")


def _banned_word_pattern(word: str) -> re.Pattern[str]:
    # Underscore is deliberately NOT a boundary character: `client/_wire.py`
    # and `_ENVELOPE_TS` must be caught exactly like the plain word in prose.
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(word)}(?![A-Za-z0-9])", re.IGNORECASE)


def _banned_word_files() -> list[pathlib.Path]:
    paths = []
    for package in BANNED_WORD_PACKAGES:
        for path in sorted((ROOT / package).rglob("*.py")):
            if "__pycache__" not in path.parts:
                paths.append(path)
    paths.extend(sorted((ROOT / "dashboard" / "static").glob("*.js")))
    return paths


def test_no_banned_word_appears_in_code_comments_or_file_names():
    violations = []
    for path in _banned_word_files():
        relative = path.relative_to(ROOT)
        for word in BANNED_WORDS:
            pattern = _banned_word_pattern(word)
            if pattern.search(path.name):
                violations.append(f"{relative} — file name contains {word!r}")
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line):
                    violations.append(f"{relative}:{number}: {word!r}")
    assert violations == []


def test_no_banned_identifier_appears_anywhere():
    """Case-sensitive, unlike Gate 3: these are exact class names, not prose."""
    violations = []
    for path in _banned_word_files():
        relative = path.relative_to(ROOT)
        for name in BANNED_IDENTIFIERS:
            pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])")
            if pattern.search(path.name):
                violations.append(f"{relative} — file name contains {name!r}")
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line):
                    violations.append(f"{relative}:{number}: {name!r}")
    assert violations == []
