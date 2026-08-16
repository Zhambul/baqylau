"""Import safety for the canonical runtime and direct plugin entries."""

from __future__ import annotations

import os
import subprocess
import sys

from conftest import REPOSITORY_ROOT

CANONICAL_MODULES = (
    "harness.impl",
    "core.daemon_client",
    "harness.hooks.client",
    "terminal.panes.mirror_process",
    "terminal.panes.scoreboard_process",
    "terminal.panes.client",
    "api.server",
    "harness.impl.claude_code.plugin",
    "harness.impl.claude_code.hooks.entry",
    "harness.impl.claude_code.hooks.gateway",
    "harness.impl.codex.hooks.gateway",
    "harness.impl.claude_code.hooks.foreground",
    "harness.impl.claude_code.hooks.statusline",
    "harness.impl.codex.plugin",
    "harness.impl.codex.hooks.entry",
)

IMPORT_PROGRAM = """
import importlib
import sys
import terminal.impl
module = sys.argv[1]
sys.argv = ['import-safety-test']
def fail(*arguments, **keywords):
    raise AssertionError('terminal resolved at import time')
terminal.impl.resolve = fail
importlib.import_module(module)
print('OK')
"""


def _environment():
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("KITTY_", "CLAUDE_"))
    }


def test_canonical_modules_have_no_import_time_terminal_or_argument_work():
    for module in CANONICAL_MODULES:
        result = subprocess.run(
            [sys.executable, "-c", IMPORT_PROGRAM, module],
        cwd=REPOSITORY_ROOT,
            env=_environment(),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0 and "OK" in result.stdout, (
            f"import of {module} had side effects:\n{result.stderr}"
        )


def test_hook_entries_do_not_load_presentation_or_legacy_semantic_stores():
    program = """
import importlib
import sys
importlib.import_module(sys.argv[1])
forbidden = {
    'core.ops', 'core.state', 'core.sessionapi', 'core.mdrender',
    'dashboard.presenter', 'terminal.mirror.presenter', 'pygments', 'wenmode',
}
loaded = sorted(forbidden.intersection(sys.modules))
if loaded:
    raise SystemExit(','.join(loaded))
"""
    for module in (
        "harness.impl.claude_code.hooks.entry",
        "harness.impl.claude_code.hooks.statusline",
        "harness.impl.codex.hooks.entry",
    ):
        result = subprocess.run(
            [sys.executable, "-c", program, module],
            cwd=REPOSITORY_ROOT,
            env=_environment(),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, f"{module} loaded: {result.stderr}"


def test_audit_write_path_does_not_import_its_report_tier():
    """A writer records diagnostics; it never reads them back.

    `diagnostics/read.py` is the daemon's tier — typed queries the dashboard
    renders. Every writer outside the daemon is a process that lives for
    milliseconds, so importing the reader buys it a sqlite tier it cannot use.
    (The daemon itself, `api.server`, legitimately holds both halves.)
    """
    program = """
import importlib
import sys
importlib.import_module(sys.argv[1])
if 'diagnostics.read' in sys.modules:
    raise SystemExit('diagnostics.read loaded')
"""
    writers = tuple(module for module in CANONICAL_MODULES if module != "api.server")
    for module in ("diagnostics.record", *writers):
        result = subprocess.run(
            [sys.executable, "-c", program, module],
            cwd=REPOSITORY_ROOT,
            env=_environment(),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, f"{module}: {result.stderr}"
