"""Ensure the one long-lived canonical application host is running."""

from __future__ import annotations

import os
import subprocess
import sys
import time

from dashboard import cli
from app.services import ApplicationHostControl

START_TIMEOUT_SECONDS = 2.0


class ApplicationHost(ApplicationHostControl):
    def ensure_running(self) -> None:
        if cli.holder():
            return
        entry_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "bin",
            "baqylau-dashboard.py",
        )
        subprocess.Popen(
            [sys.executable, entry_path, "serve"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        deadline = time.monotonic() + START_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if cli.holder():
                return
            time.sleep(0.05)
        raise RuntimeError("canonical application host did not start")
