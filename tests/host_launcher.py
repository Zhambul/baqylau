# Copyright (c) 2026 Zhambyl Yermagambet
"""Give the tests a host executable that starts the checkout's host with the test interpreter.

The checkout's shebangs name the owner's venv, or `python3` on the PATH after
`retarget_python.py --revert`. A clean test environment has neither, so the
tests start the host as an installed host starts: with its own interpreter.
"""

import functools
import hashlib
import os
import shlex
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "bin" / "baqylau_dashboard.py"
NAME_LENGTH = 16
EXECUTABLE_MODE = 0o755
COMMAND = f'exec {shlex.join((sys.executable, str(ENTRY)))} "$@"\n'


@functools.cache
def host_executable() -> Path:
    """Write the launcher once for each interpreter and checkout.

    Returns:
        The launcher path.

    """
    name = hashlib.sha256(COMMAND.encode()).hexdigest()[:NAME_LENGTH]
    directory = Path(tempfile.gettempdir()) / f"baqylau-test-host-{name}"
    directory.mkdir(exist_ok=True)
    launcher = directory / "baqylau-dashboard"
    written = directory / f"baqylau-dashboard.{os.getpid()}"
    written.write_text(f"#!/bin/sh\n{COMMAND}", encoding="utf-8")
    written.chmod(EXECUTABLE_MODE)
    written.replace(launcher)
    return launcher
