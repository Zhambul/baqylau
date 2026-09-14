# Copyright (c) 2026 Zhambyl Yermagambet
"""Check native sign-in failure and usage recovery through HTTP."""

import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from harness.impl.claude_code.usage import live
from harness.impl.claude_code.usage.rows import ClaudeCodeUsage
from harness.runtime import HarnessRuntimeConfig
from harness.services.usage import ApplicationUsageState
from tests import http_test_assets, http_test_controls
from tests.plugin_tests.support_launch import claude_usage_response
from tests.provider_graph import ProviderGraph

USAGE_ROWS = "usage_rows"
EXECUTABLE_MODE = 0o700

NATIVE_CLI = """
import json, os, pathlib, sys
state = json.loads((pathlib.Path(os.environ["CLAUDE_CONFIG_DIR"]) / "state.json").read_text())
if sys.argv[1] == "auth":
    print(json.dumps({"loggedIn": state["signed_in"]}))
    sys.exit(0 if state["signed_in"] else 1)
request = json.loads(sys.stdin.readline())
print(json.dumps({"type": "control_response", "response": {
    "subtype": "success", "request_id": request["request_id"], "response": state["usage"]
}}), flush=True)
"""


# Harness limit: claude_code only. Its native get_usage response has no sign-in error field.
@pytest.mark.parametrize("signed_in", [False, True])
def test_unavailable_usage_reports_native_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, signed_in: bool,
) -> None:
    """Separate signed-out accounts from accounts with no plan limits."""
    monkeypatch.setattr(live, "_cache", [])
    runtime = _native_cli(tmp_path)
    _state(tmp_path, {"rate_limits_available": False}, signed_in=signed_in)
    row = ClaudeCodeUsage(runtime).read()[0]
    assert bool(row.authentication_error) is not signed_in
    assert bool(row.collection_error) is signed_in
    assert row.windows == ()


# Harness limit: claude_code only. Replay sign-in recovery through its native control protocol.
def test_sign_in_restores_usage_without_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Publish limits after sign-in without keeping the earlier auth error."""
    monkeypatch.setattr(live, "_cache", [])
    clock = Mock()
    clock.time.side_effect = [100, 106]
    monkeypatch.setattr(live, "time", clock)
    _state(tmp_path, {"rate_limits_available": False}, signed_in=False)
    application = ProviderGraph()
    application.provider("usage_state", ApplicationUsageState).source = ClaudeCodeUsage(_native_cli(tmp_path))
    with http_test_assets.running_server(application) as server:
        application.provider("usage_state", ApplicationUsageState).refresh()
        before = json.loads(http_test_controls.get(server, "/api/application").body.raw)
        _state(tmp_path, claude_usage_response().model_dump(mode="json"), signed_in=True)
        application.provider("usage_state", ApplicationUsageState).refresh()
        after = json.loads(http_test_controls.get(server, "/api/application").body.raw)
    assert "claude auth login" in before[USAGE_ROWS][0]["authentication_error"]
    assert before[USAGE_ROWS][0]["collection_error"] is None
    assert after[USAGE_ROWS][0]["authentication_error"] is None
    assert after[USAGE_ROWS][0]["collection_error"] is None
    assert after[USAGE_ROWS][0]["windows"]


def _native_cli(directory: Path) -> HarnessRuntimeConfig:
    executable = directory / "claude"
    executable.write_text(f"#!{sys.executable}\n{NATIVE_CLI}")
    executable.chmod(EXECUTABLE_MODE)
    return HarnessRuntimeConfig(str(executable), directory)


def _state(directory: Path, usage: dict[str, object], *, signed_in: bool) -> None:
    (directory / "state.json").write_text(json.dumps({"signed_in": signed_in, "usage": usage}))
