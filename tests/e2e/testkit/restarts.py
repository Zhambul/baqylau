"""A typed boundary for the installed macOS daemon."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass

from sdk.client import wait_for

HEALTH_URL = "http://127.0.0.1:8377/api/health"
LAUNCH_AGENT_LABEL = "top.zhambyl.baqylau-dashboard"


def _health_process_id() -> int | None:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=1.0) as response:
            document = json.loads(response.read())
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return None
    process_id = document.get("process_id") if isinstance(document, dict) else None
    return process_id if isinstance(process_id, int) and process_id > 1 else None


def _launch_agent_state() -> str:
    result = subprocess.run(
        (
            "launchctl",
            "print",
            f"gui/{os.getuid()}/{LAUNCH_AGENT_LABEL}",
        ),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"launch agent {LAUNCH_AGENT_LABEL!r} is unavailable: {result.stderr.strip()}")
    return result.stdout


@dataclass
class InstalledDaemonRestart:
    old_process_id: int | None = None
    new_process_id: int | None = None

    def stop_and_wait_for_replacement(self) -> None:
        process_id = _health_process_id()
        if process_id is None:
            raise AssertionError("the installed dashboard health endpoint is unavailable")
        self.old_process_id = process_id
        os.kill(process_id, signal.SIGTERM)
        self.new_process_id = wait_for(
            f"installed daemon process {process_id} to be replaced",
            lambda: self._replacement(process_id),
            timeout=20.0,
            interval=0.25,
        )

    @staticmethod
    def _replacement(old_process_id: int) -> int | None:
        process_id = _health_process_id()
        return process_id if process_id is not None and process_id != old_process_id else None

    def assert_new_process(self) -> None:
        assert self.old_process_id is not None
        assert self.new_process_id is not None
        assert self.new_process_id != self.old_process_id
        assert _health_process_id() == self.new_process_id

    def assert_automatic_launch_agent(self) -> None:
        state = _launch_agent_state()
        assert "state = running" in state
        assert f"pid = {self.new_process_id}" in state
        properties = next(
            (line.strip() for line in state.splitlines() if line.strip().startswith("properties =")),
            "",
        )
        assert "keepalive" in properties
        assert "runatload" in properties
