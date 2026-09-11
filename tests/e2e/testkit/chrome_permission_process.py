# Copyright (c) 2026 Zhambyl Yermagambet
"""Build the isolated application for the Claude-in-Chrome E2E test."""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from api.runtime import ApplicationConfig
from harness import runtime as harness_runtime
from tests.e2e.testkit.process import ApplicationProcess
from tests.harness_names import CLAUDE_CODE_HARNESS, CODEX_HARNESS

if TYPE_CHECKING:
    import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
FAKE_CLAUDE = Path(__file__).resolve().parents[1] / "fixtures" / "fake_claude_chrome.py"
EXECUTABLE_FILE_MODE = 0o755


@dataclass(frozen=True)
class ChromeApplicationFactory:
    """Build the test application and its fake Claude executable."""

    temporary_path: Path

    def start(self, monkeypatch: pytest.MonkeyPatch) -> ApplicationProcess:
        """Start the isolated application.

        Returns:
            The application process configured with the fake Claude executable.

        """
        wrapper = self._write_claude_wrapper()
        environment = self._environment()
        for name, variable_content in environment.items():
            monkeypatch.setenv(name, variable_content)
        return ApplicationProcess.start(self._config(wrapper, environment))

    def _write_claude_wrapper(self) -> Path:
        wrapper = self.temporary_path / "claude"
        python_executable = REPOSITORY_ROOT / ".venv" / "bin" / "python"
        wrapper.write_text(
            f'#!/bin/zsh\nexec -a claude {shlex.quote(str(python_executable))} '
            f'{shlex.quote(str(FAKE_CLAUDE))} "$@"\n',
            encoding="utf-8",
        )
        wrapper.chmod(EXECUTABLE_FILE_MODE)
        return wrapper

    def _environment(self) -> dict[str, str]:
        return {
            **os.environ,
            "BAQYLAU_E2E_CHROME_ACCEPTED": str(
                self.temporary_path / "chrome-accepted.txt",
            ),
            "BAQYLAU_USAGE_INITIAL_DELAY_SECONDS": "3600",
        }

    def _config(
        self,
        wrapper: Path,
        environment: dict[str, str],
    ) -> ApplicationConfig:
        return ApplicationConfig(
            data_directory=self.temporary_path / "data",
            port=0,
            terminal="pty",
            notify_telegram=False,
            notify_webpush=False,
            harness_runtime_configs=harness_runtime.HarnessRuntimeConfigs(
                (
                    harness_runtime.HarnessRuntimeEntry(
                        CLAUDE_CODE_HARNESS,
                        harness_runtime.HarnessRuntimeConfig(
                            str(wrapper),
                            self.temporary_path / "claude",
                        ),
                    ),
                    harness_runtime.HarnessRuntimeEntry(
                        CODEX_HARNESS,
                        harness_runtime.default_harness_runtime_configs().for_harness(
                            CODEX_HARNESS,
                        ),
                    ),
                ),
            ),
            base_environment=environment,
        )
