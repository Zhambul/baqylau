# Copyright (c) 2026 Zhambyl Yermagambet
"""One-shot private model execution and provider selection."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING

from harness.models.usage import (
    UsageRow,
    UsageWindow,
    UsageWindowScope,
)
from inference import (
    commands as inference_commands,
    contract as inference_contract,
)
from terminal.models import tabs
from tests.harness_names import CLAUDE_CODE_HARNESS, CODEX_HARNESS
from tests.inference_support import Audit, InferenceTerminal, Usage, factory

if TYPE_CHECKING:
    from domain.ids import HarnessName

HIGH_USAGE_PERCENT = 90
NEARLY_EXHAUSTED_PERCENT = 99
MODEL_PROMPT = "name this"
CLAUDE_EXECUTABLE = "claude"
CODEX_EXECUTABLE = "codex"
FIRST_MODEL_WINDOW_ID = "model-1"
TEST_SESSION_ID = "session-one"


def usage_row(harness: HarnessName, used_percent: Decimal) -> UsageRow:
    """Create one account usage window for a provider test.

    Returns:
        The usage row with the supplied provider and used percentage.

    """
    return UsageRow(
        harness=harness,
        account_id=None,
        display_name=str(harness),
        switchable=False,
        default_for_launch=False,
        plan=None,
        windows=(
            UsageWindow(
                key="limit",
                label="limit",
                used_percent=used_percent,
                resets_at=None,
                duration_minutes=None,
                scope=UsageWindowScope.ACCOUNT,
                model_name=None,
            ),
        ),
        scheduling_score=None,
        scheduling_allowed=False,
        limit=None,
        authentication_error=None,
    )


def test_small_model_prefers_provider_with_more() -> None:
    """Verify small model prefers the provider with more remaining capacity."""
    terminal = InferenceTerminal(('{"title":"Capacity aware session title"}',))
    model_factory = factory(
        terminal,
        Usage(
            (
                usage_row(CODEX_HARNESS, Decimal(HIGH_USAGE_PERCENT)),
                usage_row(CLAUDE_CODE_HARNESS, Decimal(10)),
            ),
        ),
    )

    response = model_factory.small().send(inference_contract.ModelPromptRequest(MODEL_PROMPT))

    launch = terminal.opened_tabs[0]
    assert (
        response.text,
        launch.command[0],
        launch.environment,
        terminal.closed_tabs,
    ) == (
        "Capacity aware session title",
        CLAUDE_EXECUTABLE,
        (tabs.EnvironmentVariable(inference_commands.INTERNAL_MODEL_VARIABLE, "1"),),
        [FIRST_MODEL_WINDOW_ID],
    )
    assert {"--safe-mode", "--no-session-persistence", "--tools"} <= set(launch.command)


def test_capacity_uses_most_exhausted_known() -> None:
    """Verify capacity uses the most exhausted known window."""
    codex = usage_row(CODEX_HARNESS, Decimal(5))
    codex = replace(
        codex,
        windows=(
            *codex.windows,
            replace(
                codex.windows[0],
                key="weekly",
                used_percent=Decimal(NEARLY_EXHAUSTED_PERCENT),
            ),
        ),
    )
    terminal = InferenceTerminal(('{"title":"Most constrained usage window"}',))

    factory(
        terminal,
        Usage((codex, usage_row(CLAUDE_CODE_HARNESS, Decimal(HIGH_USAGE_PERCENT)))),
    ).small().send(inference_contract.ModelPromptRequest(MODEL_PROMPT))

    assert terminal.opened_tabs[0].command[0] == CLAUDE_EXECUTABLE


def test_authentication_failure_excludes_that() -> None:
    """Verify authentication failure excludes that provider."""
    codex = replace(
        usage_row(CODEX_HARNESS, Decimal(0)),
        authentication_error="authentication failed",
    )
    terminal = InferenceTerminal(('{"title":"Authenticated fallback provider"}',))

    provider = factory(terminal, Usage((codex,))).small()
    provider.send(inference_contract.ModelPromptRequest(MODEL_PROMPT))

    assert terminal.opened_tabs[0].command[0] == CLAUDE_EXECUTABLE


def test_rate_limited_provider_falls_back() -> None:
    """Verify rate limited provider falls back to a fresh other provider window."""
    audit = Audit()
    terminal = InferenceTerminal(
        (
            "rate limit exceeded",
            '{"title":"Fallback provider session title"}',
        ),
    )

    response = (
        factory(terminal, audit=audit)
        .small()
        .send(
            inference_contract.ModelPromptRequest(MODEL_PROMPT, TEST_SESSION_ID),
        )
    )

    codex = terminal.opened_tabs[0]
    assert (
        response.text,
        [launch.command[0] for launch in terminal.opened_tabs],
        terminal.closed_tabs,
        codex.working_directory != terminal.opened_tabs[1].working_directory,
        audit.errors,
    ) == (
        "Fallback provider session title",
        [CODEX_EXECUTABLE, CLAUDE_EXECUTABLE],
        [FIRST_MODEL_WINDOW_ID, "model-2"],
        True,
        [],
    )
    assert {"--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check", "read-only"} <= set(
        codex.command,
    )
    assert "resume" not in codex.command
