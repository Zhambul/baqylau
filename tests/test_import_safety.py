"""Import safety for the canonical runtime and direct plugin entries."""

from __future__ import annotations

import os
import subprocess
import sys

from conftest import REPOSITORY_ROOT

CANONICAL_MODULES = (
    "app.plugins",
    "app.daemon_client",
    "app.hook_client",
    "app.terminal_process",
    "app.scoreboard_process",
    "app.terminal_panes",
    "api.server",
    "plugins.claude_code.plugin",
    "plugins.claude_code.canonical_hook",
    "plugins.claude_code.hooks",
    "plugins.codex.hooks",
    "plugins.claude_code.foreground",
    "plugins.claude_code.statusline",
    "plugins.codex.plugin",
    "plugins.codex.canonical_hook",
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
        "plugins.claude_code.canonical_hook",
        "plugins.claude_code.statusline",
        "plugins.codex.canonical_hook",
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
    program = """
import importlib
import sys
importlib.import_module(sys.argv[1])
if 'core.auditcli' in sys.modules:
    raise SystemExit('core.auditcli loaded')
"""
    for module in ("core.audit", *CANONICAL_MODULES):
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
