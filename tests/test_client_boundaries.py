# Copyright (c) 2026 Zhambyl Yermagambet
"""Verify terminal client architecture and behavior."""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from types import MappingProxyType
from typing import TypeGuard

from tests.client_test_render_support import imported_names
from tests.test_client_architecture import _parsed_nodes

ROOT = Path(__file__).resolve().parents[1]


CLIENT = ROOT / "client"


SHARED = (
    "_daemon.py",
    "_daemon_exchange.py",
    "_extension_actions.py",
    "_extension_changes.py",
    "_extension_focus.py",
    "_extension_input.py",
    "_extension_paint.py",
    "_extension_pane.py",
    "_extension_sections.py",
    "_extension_selector.py",
    "_handoff.py",
    "_handoff_documents.py",
    "_handoff_lock.py",
    "_handoff_paths.py",
    "_handoff_storage.py",
    "_http.py",
    "_model.py",
    "_model_actor.py",
    "_model_attention.py",
    "_model_base.py",
    "_model_entry.py",
    "_model_session.py",
    "_model_session_feed.py",
    "_model_session_state.py",
    "_model_shell.py",
    "_model_terminal.py",
    "_model_terminal_items.py",
    "_model_terminal_text.py",
    "_pane_connection.py",
    "_pane_rendering.py",
    "_pane_signals.py",
    "_pane_state.py",
    "_render.py",
    "_render_assignments.py",
    "_render_blocks.py",
    "_render_compose.py",
    "_render_diff_models.py",
    "_render_diff_paint.py",
    "_render_diff_parse.py",
    "_render_entries.py",
    "_render_extension_blocks.py",
    "_render_extension_items.py",
    "_render_extension_table.py",
    "_render_extension_text.py",
    "_render_files.py",
    "_render_line_numbers.py",
    "_render_numbers.py",
    "_render_rows.py",
    "_render_score_details.py",
    "_render_scoreboard.py",
    "_render_shells.py",
    "_render_span_operations.py",
    "_render_statistics.py",
    "_render_styles.py",
    "_render_tasks.py",
    "_render_tools.py",
    "_render_width.py",
    "_render_wrap.py",
    "_selector_keys.py",
)


CLAUDE_HOOK = "claude_hook.py"
TERMINAL_EXTENSION_PANE = "terminal_extension_pane.py"
TERMINAL_EXTENSION_SELECTOR = "terminal_extension_selector.py"


CLAUDE_STATUSLINE = "claude_statusline.py"


CLAUDE_OTEL = "claude_otel.py"


CODEX_HOOK = "codex_hook.py"


TERMINAL_PANE = "terminal_pane.py"


TERMINAL_KEYS = "terminal_keys.py"


TERMINAL_VIEW = "terminal_view.py"


TERMINAL_CONTENT = "terminal_content.py"


PUBLISHED = (
    CLAUDE_HOOK, CLAUDE_STATUSLINE, CODEX_HOOK, TERMINAL_KEYS, TERMINAL_VIEW, TERMINAL_CONTENT,
    TERMINAL_EXTENSION_SELECTOR,
)


LAUNCHED = (CLAUDE_OTEL, TERMINAL_PANE, TERMINAL_EXTENSION_PANE)


PYDANTIC_PACKAGE = "pydantic"


PYDANTIC_DEPENDENCIES = (PYDANTIC_PACKAGE,)


PYTHON_FILE_PATTERN = "*.py"


OWN_DIRECTORY = "str(Path(__file__).resolve().parent)"


ROOT_ANCHORS = ("bin/baqylau_dashboard.py", "bin/baqylau_raw_events_audit.py")


CLIENT_DEPENDENCIES = MappingProxyType({
    "_handoff.py": PYDANTIC_DEPENDENCIES,
    "_handoff_documents.py": PYDANTIC_DEPENDENCIES,
    "_handoff_storage.py": PYDANTIC_DEPENDENCIES,
    "_model_base.py": PYDANTIC_DEPENDENCIES,
    "_model_entry.py": PYDANTIC_DEPENDENCIES,
    "_extension_actions.py": PYDANTIC_DEPENDENCIES,
    "_extension_changes.py": PYDANTIC_DEPENDENCIES,
    "_extension_selector.py": PYDANTIC_DEPENDENCIES,
    "_model_terminal.py": PYDANTIC_DEPENDENCIES,
    "_pane_connection.py": PYDANTIC_DEPENDENCIES,
    "terminal_keys.py": PYDANTIC_DEPENDENCIES,
    "terminal_pane.py": PYDANTIC_DEPENDENCIES,
})


def test_client_files_stay_in_one_directory() -> None:
    """Keep client programs and support modules in one directory (R1).

    A support module can split a large client without making ``client`` a
    package. A subdirectory would make each program walk up to a shared module.
    That walk can fail before the program can report the failure.
    """
    assert not (CLIENT / "__init__.py").exists(), "client/ is not importable BY us"
    directories = [entry for entry in CLIENT.iterdir() if entry.is_dir()]
    assert directories == [entry for entry in directories if entry.name == "__pycache__"]
    files = {path.name for path in CLIENT.glob(PYTHON_FILE_PATTERN)}
    assert {name for name in files if name.startswith("_")} == set(SHARED)
    # A declared program must have one file, and every public client file must
    # be declared.
    assert files - set(SHARED) == set(PUBLISHED) | set(LAUNCHED)


def test_clients_import_only_standard_library() -> None:
    """R2 — the rule the other rules are for.

    What crosses this boundary is a URL, seven header names, a port and the
    process's own stdin. Importing the application to obtain them cost 111 ms per
    hook process on top of the interpreter floor (`bin/retarget-python` exists
    because ~140 ms of per-hook overhead was already intolerable), and coupled
    every hook delivery to every import under `harness/impl/`.
    """
    siblings = {path.stem for path in CLIENT.glob(PYTHON_FILE_PATTERN)}
    violations: list[str] = []
    for path in sorted(CLIENT.glob(PYTHON_FILE_PATTERN)):
        for imported in imported_names(path):
            root = imported.split(".")[0]
            if root in _permitted_roots(siblings, path):
                continue
            violations.append(f"{path.name} imports {imported}")
    assert not violations


def test_nothing_but_two_dev_entries_walks_up() -> None:
    """Verify nothing but two dev entries walks up to the repository root.

    R3 — replaces the old rule, which counted the levels and required the
        anchor to name that many.

        That rule was the best available check on a design where 16 files each held a
        depth count, and it still could not have stopped the outage it was written
        for: a count is only wrong AFTER the move, the wrong count still resolves,
        and the failure lands in a process nobody is watching. A file may now name
        nothing but its own directory.
    """
    violations = [
        violation
        for path in sorted(ROOT.rglob(PYTHON_FILE_PATTERN))
        if _checks_root_anchor(path)
        for violation in _root_anchor_violations(path)
    ]
    assert not violations
    assert all((ROOT / name).is_file() for name in ROOT_ANCHORS)


def _checks_root_anchor(path: Path) -> bool:
    """Return whether a file must not walk up to the repository root.

    Returns:
        Whether a file must not walk up to the repository root.

    """
    if "__pycache__" in path.parts or ".venv" in path.parts:
        return False
    relative_path = path.relative_to(ROOT).as_posix()
    return relative_path not in ROOT_ANCHORS and not relative_path.startswith("tests/")


def _root_anchor_violations(path: Path) -> list[str]:
    """Return root-anchor violations from a Python file.

    Returns:
        Root-anchor violations from a Python file.

    """
    relative_path = path.relative_to(ROOT).as_posix()
    violations: list[str] = []
    for node in _parsed_nodes(path):
        if _is_root_anchor_call(node):
            anchor = ast.unparse(node.args[1])
            if anchor != OWN_DIRECTORY:
                violations.append(f"{relative_path} anchors on {anchor}")
    return violations


def _is_root_anchor_call(node: ast.AST) -> TypeGuard[ast.Call]:
    """Return whether a node inserts a repository-root import path.

    Returns:
        Whether a node inserts a repository-root import path.

    """
    return isinstance(node, ast.Call) and ast.unparse(node.func) == "sys.path.insert"


def _permitted_roots(siblings: set[str], path: Path) -> set[str]:
    """Return standard and declared import roots for one client file.

    Returns:
        Standard and declared import roots for one client file.

    """
    permitted = set(sys.stdlib_module_names)
    permitted.update(siblings)
    permitted.update(CLIENT_DEPENDENCIES.get(path.name, set()))
    return permitted
