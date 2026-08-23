"""Checks for the E2E work adapter."""

from tests.e2e.testkit.work import (
    WorkRequest,
    _assignment_actor_name,
    _delegation_prompt,
    _parallel_delegation_prompt,
    _worker_name,
)


def test_codex_worker_name_uses_the_v2_name_grammar():
    assert _worker_name("Greeting work 42!") == "e2e_greeting_work_42"


def test_parallel_work_prompt_keeps_native_tools_behind_the_adapter():
    requests = (
        WorkRequest("alpha work", "Reply alpha."),
        WorkRequest("beta work", "Reply beta."),
    )

    codex = _parallel_delegation_prompt("codex", requests)
    claude = _parallel_delegation_prompt("claude_code", requests)

    assert codex.count("multi_agent_v2__spawn_agent") == 1
    assert "multi_agent_v1__" not in codex
    assert "Agent tool" in claude
    assert "e2e_alpha_work" in codex
    assert "WORK NAME: beta work" in claude
    assert "Do not set name" in claude
    assert _assignment_actor_name("codex", "alpha work") == "e2e alpha work"
    assert _assignment_actor_name("claude_code", "alpha work") == "e2e_alpha_work"


def test_codex_delegation_protects_an_explicit_skill_mention_from_the_lead():
    prompt = _delegation_prompt(
        "codex",
        "skill work",
        "$baqylau-e2e-communication",
        (),
    )

    assert "$baqylau-e2e-communication" not in prompt
    assert '"\\u0024baqylau-e2e-communication"' in prompt
    assert "Decode WORK MESSAGE JSON as JSON" in prompt
