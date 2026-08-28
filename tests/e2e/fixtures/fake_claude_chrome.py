#!/usr/bin/env python3
"""A Claude terminal fixture that asks for a Chrome site permission."""

from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path

SESSION_ID = "00000000-0000-4000-8000-000000000738"
CALL_ID = "chrome-navigation-738"


def _hook(payload: dict[str, object]) -> dict[str, object]:
    port = os.environ["BAQYLAU_DASHBOARD_PORT"]
    window_id = os.environ["BAQYLAU_PTY_WINDOW_ID"]
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/harnesses/claude_code/hooks",
        data=(json.dumps(payload) + "\n").encode(),
        headers={
            "Content-Type": "application/json",
            "X-Baqylau-Terminal-Window": window_id,
            "X-Baqylau-Client-Process": str(os.getpid()),
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError(f"hook delivery returned {response.status}")
        body = response.read()
    return json.loads(body) if body else {}


def _payload(transcript: str, hook_name: str, **values: object) -> dict[str, object]:
    return {
        "session_id": SESSION_ID,
        "transcript_path": transcript,
        "cwd": os.getcwd(),
        "hook_event_name": hook_name,
        **values,
    }


def main() -> None:
    transcript = str(Path(os.getcwd()) / "fake-claude-chrome.jsonl")
    Path(transcript).write_text("", encoding="utf-8")
    _hook(_payload(transcript, "SessionStart", source="startup"))
    _hook(
        _payload(
            transcript,
            "PreToolUse",
            tool_use_id=CALL_ID,
            tool_name="mcp__claude-in-chrome__navigate",
            tool_input={"url": "https://example.com"},
        )
    )
    session_update = {
        "type": "addRules",
        "rules": [
            {
                "toolName": "ClaudeInChromeDomain",
                "ruleContent": "example.com",
            }
        ],
        "behavior": "allow",
        "destination": "session",
    }
    reply = _hook(
        _payload(
            transcript,
            "PermissionRequest",
            tool_name="mcp__claude-in-chrome__navigate",
            tool_input={"url": "https://example.com"},
            permission_suggestions=[session_update],
        )
    )
    output = reply.get("hookSpecificOutput")
    if not isinstance(output, dict):
        raise RuntimeError("Chrome permission output is absent")
    decision = output.get("decision")
    if not isinstance(decision, dict):
        raise RuntimeError("Chrome permission decision is absent")
    if decision.get("behavior") != "allow":
        raise RuntimeError("Chrome permission was not allowed")
    if decision.get("updatedPermissions") != [session_update]:
        raise RuntimeError("Chrome session permission was not returned")
    Path(os.environ["BAQYLAU_E2E_CHROME_ACCEPTED"]).write_text(
        json.dumps(reply),
        encoding="utf-8",
    )
    _hook(
        _payload(
            transcript,
            "PostToolUse",
            tool_use_id=CALL_ID,
            tool_name="mcp__claude-in-chrome__navigate",
            tool_input={"url": "https://example.com"},
            tool_response={"content": "Example Domain loaded"},
        )
    )
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
