# Copyright (c) 2026 Zhambyl Yermagambet
"""Checks for the E2E work adapter."""

import json
import os
from pathlib import Path
from unittest import mock

from harness.impl.opencode2 import launch_variant
from terminal.models.tabs import EnvironmentVariable
from tests.e2e.testkit import work_delegation, work_names
from tests.e2e.testkit.work import _parallel_delegation_prompt
from tests.e2e.testkit.work_models import WorkRequest

FLASH_MODEL = "opencode-go/deepseek-v4.1-flash"
HIGH_EFFORT = "high"
STATE_HOME = "XDG_STATE_HOME"


def test_codex_worker_name_uses_v2_name_grammar() -> None:
    """Verify codex worker name uses the v2 name grammar."""
    assert work_names.worker_name("Greeting work 42!") == "e2e_greeting_work_42"


def test_parallel_work_prompt_keeps_native_tools() -> None:
    """Verify parallel work prompt keeps native tools behind the adapter."""
    requests = (
        WorkRequest("alpha work", "Reply alpha."),
        WorkRequest("beta work", "Reply beta."),
    )

    codex = _parallel_delegation_prompt("codex", requests)
    claude = _parallel_delegation_prompt("claude_code", requests)

    assert (
        codex.count("spawn_agent"),
        "multi_agent_v1__" not in codex,
        "Agent tool" in claude,
        "e2e_alpha_work" in codex,
        "WORK NAME: beta work" in claude,
        "Do not set name" in claude,
        work_names.assignment_actor_name("codex", "alpha work"),
        work_names.assignment_actor_name("claude_code", "alpha work"),
    ) == (1, True, True, True, True, True, "e2e alpha work", "e2e_alpha_work")


def test_codex_delegation_protects_explicit_skill() -> None:
    """Verify codex delegation protects an explicit skill mention from the lead."""
    prompt = work_delegation.delegation_prompt(
        "codex",
        WorkRequest("skill work", "$baqylau-e2e-communication"),
    )

    assert "$baqylau-e2e-communication" not in prompt
    assert r'"\u0024baqylau-e2e-communication"' in prompt
    assert "Decode WORK MESSAGE JSON as JSON" in prompt


def test_opencode_effort_is_recorded_natively(tmp_path: Path) -> None:
    """Keep the person's own model state while the launch effort is recorded.

    The native default has priority over the launch configuration, so the
    effort is written to the state file. That file also holds the person's
    recent and favourite models, and it is written back whole.
    """
    state = tmp_path / "state"
    target = state / "opencode" / "model.json"
    target.parent.mkdir(parents=True)
    recent = [{"providerID": "openai", "modelID": "gpt-5.3-codex"}]
    target.write_text(json.dumps({"recent": recent, "variant": {"opencode-go/other-model": HIGH_EFFORT}}))

    reason = launch_variant.apply((EnvironmentVariable(STATE_HOME, str(state)),), FLASH_MODEL, "low")

    saved = json.loads(target.read_text())
    assert reason is None
    assert saved["variant"] == {"opencode-go/other-model": HIGH_EFFORT, FLASH_MODEL: "low"}
    assert saved["recent"] == recent


def test_opencode_effort_uses_the_session_state(tmp_path: Path) -> None:
    """Record the effort where the new session reads it, and nowhere else.

    The session gets a state home in its own environment. The effort goes there,
    because the session reads the effort from its own environment and the state
    home of this process belongs to the person.
    """
    own = tmp_path / "own"
    session = tmp_path / "session"

    with mock.patch.dict(os.environ, {STATE_HOME: str(own)}):
        reason = launch_variant.apply(
            (EnvironmentVariable(STATE_HOME, str(session)),), FLASH_MODEL, HIGH_EFFORT,
        )

    saved = json.loads((session / "opencode" / "model.json").read_text())
    assert reason is None
    assert not own.exists()
    assert saved["variant"] == {FLASH_MODEL: HIGH_EFFORT}
