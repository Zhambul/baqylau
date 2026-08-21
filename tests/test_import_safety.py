"""Import safety for the canonical runtime and direct plugin entries."""

from __future__ import annotations

import os
import subprocess
import sys

from conftest import REPOSITORY_ROOT

# The processes that used to be on this list — the hook entries, the two pane
# processes, the keybinding, the status-line shim — are stdlib-only clients now
# (`client/`), and tests/test_canonical_clients.py both forbids them any import of
# ours and RUNS each one. What is left here is the daemon's own import graph.
CANONICAL_MODULES = (
    "harness.impl",
    "api.server",
    "harness.impl.claude_code.plugin",
    "harness.impl.claude_code.hooks.gateway",
    "harness.impl.claude_code.otel.gateway",
    "harness.impl.claude_code.otel.launch",
    "harness.impl.codex.hooks.gateway",
    "harness.impl.claude_code.hooks.foreground",
    "harness.impl.codex.plugin",
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


def test_hook_gateways_do_not_load_presentation_or_legacy_semantic_stores():
    """The gateways run on the HTTP thread that records a delivery, so what they
    drag in is paid per hook — and a presenter is never part of recording one.

    (The hook PROCESSES this used to check are `client/` files now: they import
    nothing of ours at all, which is checked and MEASURED next door.)
    """
    program = """
import importlib
import sys
importlib.import_module(sys.argv[1])
forbidden = {
    'core.ops', 'core.state', 'core.sessionapi', 'core.mdrender',
    'api.sessiondata.mapper', 'engine.sessiondata.entries', 'pygments', 'wenmode',
}
loaded = sorted(forbidden.intersection(sys.modules))
if loaded:
    raise SystemExit(','.join(loaded))
"""
    for module in (
        "harness.impl.claude_code.hooks.gateway",
        "harness.impl.claude_code.otel.gateway",
        "harness.impl.codex.hooks.gateway",
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
    """A writer records audit; it never reads them back.

    The reader is the daemon's own tier — typed queries the dashboard renders — and
    the write API is reached from paths that run before the graph exists, so
    importing the reader from there buys a tier that cannot be used.
    (The daemon itself, `api.server`, legitimately holds both halves.)
    """
    program = """
import importlib
import sys
importlib.import_module(sys.argv[1])
if 'audit.read' in sys.modules:
    raise SystemExit('audit.read loaded')
"""
    writers = tuple(module for module in CANONICAL_MODULES if module != "api.server")
    for module in ("audit.record", *writers):
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
