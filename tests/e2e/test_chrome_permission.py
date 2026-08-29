"""End-to-end coverage for automatic Claude-in-Chrome permissions."""

from __future__ import annotations

import os
import shlex
import sqlite3
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from api.runtime import ApplicationConfig
from domain.ids import HarnessName
from harness.runtime import (
    HarnessRuntimeConfig,
    HarnessRuntimeConfigs,
    HarnessRuntimeEntry,
    default_harness_runtime_configs,
)
from sdk.client import BaqylauClient
from tests.e2e.testkit.process import ApplicationProcess

pytestmark = [pytest.mark.timeout(60)]
ROOT = Path(__file__).resolve().parents[2]
FAKE_CLAUDE = Path(__file__).parent / "fixtures" / "fake_claude_chrome.py"


def _wait(
    description: str,
    predicate: Callable[[], bool],
    timeout: float = 20.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(description)


@pytest.fixture
def application_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ApplicationProcess]:
    wrapper = tmp_path / "claude"
    python_executable = ROOT / ".venv" / "bin" / "python"
    wrapper.write_text(
        "#!/bin/zsh\n"
        f"exec -a claude {shlex.quote(str(python_executable))} "
        f'{shlex.quote(str(FAKE_CLAUDE))} "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    accepted = tmp_path / "chrome-accepted.txt"
    data_directory = tmp_path / "data"
    environment = {
        **os.environ,
        "BAQYLAU_E2E_CHROME_ACCEPTED": str(accepted),
        "BAQYLAU_USAGE_INITIAL_DELAY_SECONDS": "3600",
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    process = ApplicationProcess.start(
        ApplicationConfig(
            data_directory=data_directory,
            port=0,
            terminal="pty",
            notify_telegram=False,
            notify_webpush=False,
            harness_runtime_configs=HarnessRuntimeConfigs(
                (
                    HarnessRuntimeEntry(
                        HarnessName.CLAUDE_CODE,
                        HarnessRuntimeConfig(str(wrapper), tmp_path / "claude"),
                    ),
                    HarnessRuntimeEntry(
                        HarnessName.CODEX,
                        default_harness_runtime_configs().for_harness(
                            HarnessName.CODEX
                        ),
                    ),
                )
            ),
            base_environment=environment,
        )
    )
    try:
        yield process
    finally:
        assert process.stop() == 0


@pytest.fixture(autouse=True)
def scenario_signoff() -> Iterator[None]:
    """Use the test-local process lifecycle instead of the shared scenario lifecycle."""
    yield


# Harness limit: claude_code only. Only Claude Code supports Chrome control.
def test_claude_chrome_permission_is_accepted_automatically(
    application_process: ApplicationProcess,
    tmp_path: Path,
) -> None:
    client = BaqylauClient(application_process.endpoint.url)
    accepted = tmp_path / "chrome-accepted.txt"
    try:
        launch = client.sessions.launch(
            "claude_code",
            workspace=str(tmp_path),
            prompt="Open https://example.com in Chrome.",
            model=None,
            effort=None,
        )
        session = client.sessions.wait_for_session(launch, timeout=15)
        assert session.session_id == "00000000-0000-4000-8000-000000000738"
        _wait(
            "Baqylau did not return the Chrome session permission",
            accepted.exists,
        )

        main_database = application_process.config.data_directory / "main.db"

        def permission_request_was_recorded() -> bool:
            with sqlite3.connect(f"file:{main_database}?mode=ro", uri=True) as connection:
                row = connection.execute(
                    "SELECT count(*) FROM raw_events "
                    "WHERE session_id=? AND source_name='PermissionRequest'",
                    (session.session_id,),
                ).fetchone()
            return row is not None and int(row[0]) == 1

        _wait(
            "the Chrome permission request was not recorded",
            permission_request_was_recorded,
        )

        def browser_action_was_recorded() -> bool:
            with sqlite3.connect(f"file:{main_database}?mode=ro", uri=True) as connection:
                browser_event = connection.execute(
                    "SELECT json_extract(payload, '$.action') "
                    "FROM canonical_events "
                    "WHERE session_id=? AND event_type='browser.interacted'",
                    (session.session_id,),
                ).fetchone()
                web_event_count = connection.execute(
                    "SELECT count(*) FROM canonical_events "
                    "WHERE session_id=? AND event_type='web.fetched'",
                    (session.session_id,),
                ).fetchone()
                browser_entry = connection.execute(
                    "SELECT json_extract(payload, '$.action') "
                    "FROM session_entries "
                    "WHERE session_id=? AND entry_type='browser'",
                    (session.session_id,),
                ).fetchone()
            return bool(
                browser_event == ("Navigate to https://example.com",)
                and web_event_count == (0,)
                and browser_entry == ("Navigate to https://example.com",)
            )

        _wait(
            "the Chrome action did not become a Browser entry",
            browser_action_was_recorded,
        )
    finally:
        client.close()
