"""One-shot private model execution and provider selection."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from domain.ids import HarnessName
from harness.models import UsageRow, UsageWindow, UsageWindowScope
from inference import DefaultModelFactory, ModelPromptRequest, ModelUnavailableError
from inference.default import INTERNAL_MODEL_VARIABLE
from terminal.models import ScreenReadRequest, ScreenReadResponse, TabCloseRequest, TabCloseResponse
from terminal.models import TabOpenRequest, TabOpenResponse
from terminal.models.values import WindowId
from tests.fake_terminal import FakeTerminal, window


class Usage:
    def __init__(self, rows: tuple[UsageRow, ...] = ()) -> None:
        self.rows = rows

    def usage_rows(self) -> tuple[UsageRow, ...]:
        return self.rows


class InferenceTerminal(FakeTerminal):
    def __init__(self, outputs: tuple[str, ...], *, stays_open: bool = False) -> None:
        super().__init__()
        self.outputs = list(outputs)
        self.stays_open = stays_open
        self.output_by_window: dict[str, str] = {}
        self.next_id = 0

    def open_tab(self, tab_open_request: TabOpenRequest) -> TabOpenResponse:
        self.opened_tabs.append(tab_open_request)
        self.next_id += 1
        window_id = f"model-{self.next_id}"
        self.output_by_window[window_id] = self.outputs.pop(0)
        return TabOpenResponse(True, WindowId(window_id))

    def windows(self):
        if not self.stays_open or not self.output_by_window:
            return ()
        return (window(next(reversed(self.output_by_window))),)

    def read_screen(self, screen_read_request: ScreenReadRequest) -> ScreenReadResponse:
        return ScreenReadResponse(True, self.output_by_window[str(screen_read_request.window_id)])

    def close_tab(self, tab_close_request: TabCloseRequest) -> TabCloseResponse:
        self.closed_tabs.append(tab_close_request.window_id)
        self.output_by_window.pop(str(tab_close_request.window_id), None)
        return TabCloseResponse(True)


def usage_row(harness: HarnessName, used_percent: Decimal) -> UsageRow:
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


def factory(terminal: InferenceTerminal, usage: Usage | None = None, *, timeout: float = 1) -> DefaultModelFactory:
    return DefaultModelFactory(
        terminal.plugin(),
        usage or Usage(),
        timeout_seconds=timeout,
        executable_available=lambda _name: True,
    )


def test_large_model_sizes_are_deliberately_unimplemented() -> None:
    model_factory = factory(InferenceTerminal(()))

    with pytest.raises(NotImplementedError):
        model_factory.big()
    with pytest.raises(NotImplementedError):
        model_factory.mid()


def test_small_model_prefers_the_provider_with_more_remaining_capacity() -> None:
    terminal = InferenceTerminal(('{"title":"Capacity aware session title"}',))
    model_factory = factory(
        terminal,
        Usage(
            (
                usage_row(HarnessName.CODEX, Decimal(90)),
                usage_row(HarnessName.CLAUDE_CODE, Decimal(10)),
            )
        ),
    )

    response = model_factory.small().send(ModelPromptRequest("name this"))

    assert response.text == "Capacity aware session title"
    launch = terminal.opened_tabs[0]
    assert launch.command[0] == "claude"
    assert "--safe-mode" in launch.command
    assert "--no-session-persistence" in launch.command
    assert "--tools" in launch.command
    assert launch.environment == ((INTERNAL_MODEL_VARIABLE, "1"),)
    assert terminal.closed_tabs == ["model-1"]


def test_capacity_uses_the_most_exhausted_known_window() -> None:
    codex = usage_row(HarnessName.CODEX, Decimal(5))
    codex = replace(
        codex,
        windows=codex.windows + (replace(codex.windows[0], key="weekly", used_percent=Decimal(99)),),
    )
    terminal = InferenceTerminal(('{"title":"Most constrained usage window"}',))

    factory(
        terminal,
        Usage((codex, usage_row(HarnessName.CLAUDE_CODE, Decimal(90)))),
    ).small().send(ModelPromptRequest("name this"))

    assert terminal.opened_tabs[0].command[0] == "claude"


def test_authentication_failure_excludes_that_provider() -> None:
    codex = replace(
        usage_row(HarnessName.CODEX, Decimal(0)),
        authentication_error="authentication failed",
    )
    terminal = InferenceTerminal(('{"title":"Authenticated fallback provider"}',))

    factory(terminal, Usage((codex,))).small().send(ModelPromptRequest("name this"))

    assert terminal.opened_tabs[0].command[0] == "claude"


def test_rate_limited_provider_falls_back_to_a_fresh_other_provider_window() -> None:
    terminal = InferenceTerminal(
        (
            "rate limit exceeded",
            '{"title":"Fallback provider session title"}',
        )
    )

    response = factory(terminal).small().send(ModelPromptRequest("name this"))

    assert response.text == "Fallback provider session title"
    assert [launch.command[0] for launch in terminal.opened_tabs] == ["codex", "claude"]
    assert terminal.closed_tabs == ["model-1", "model-2"]
    codex = terminal.opened_tabs[0]
    assert "--ephemeral" in codex.command
    assert "--ignore-user-config" in codex.command
    assert "--ignore-rules" in codex.command
    assert "--skip-git-repo-check" in codex.command
    assert "read-only" in codex.command
    assert "resume" not in codex.command
    assert terminal.opened_tabs[0].working_directory != terminal.opened_tabs[1].working_directory


def test_title_that_violates_the_requested_shape_falls_back() -> None:
    terminal = InferenceTerminal(
        (
            '{"title":"Too short"}',
            '{"title":"Fallback title has enough words"}',
        )
    )

    response = factory(terminal).small().send(ModelPromptRequest("name this"))

    assert response.text == "Fallback title has enough words"
    assert [launch.command[0] for launch in terminal.opened_tabs] == ["codex", "claude"]


def test_each_send_opens_and_closes_a_new_session() -> None:
    terminal = InferenceTerminal(
        (
            '{"title":"First fresh model title"}',
            '{"title":"Second fresh model title"}',
        )
    )
    small = factory(terminal).small()

    small.send(ModelPromptRequest("first"))
    small.send(ModelPromptRequest("second"))

    assert terminal.closed_tabs == ["model-1", "model-2"]
    assert len(terminal.opened_tabs) == 2


def test_a_valid_title_about_rate_limits_is_not_mistaken_for_provider_failure() -> None:
    terminal = InferenceTerminal(('{"title":"Handle rate limit fallback"}',))

    response = factory(terminal).small().send(ModelPromptRequest("name this"))

    assert response.text == "Handle rate limit fallback"


def test_timeout_closes_every_attempted_provider_window() -> None:
    terminal = InferenceTerminal(("", ""), stays_open=True)

    with pytest.raises(ModelUnavailableError):
        factory(terminal, timeout=-1).small().send(ModelPromptRequest("name this"))

    assert terminal.closed_tabs == ["model-1", "model-2"]


def test_exhausted_known_quotas_do_not_open_any_provider() -> None:
    terminal = InferenceTerminal(())
    exhausted = Usage(
        (
            usage_row(HarnessName.CODEX, Decimal(100)),
            usage_row(HarnessName.CLAUDE_CODE, Decimal(100)),
        )
    )

    with pytest.raises(ModelUnavailableError):
        factory(terminal, exhausted).small().send(ModelPromptRequest("name this"))

    assert terminal.opened_tabs == []
