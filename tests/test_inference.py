"""One-shot private model execution and provider selection."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import cast

import pytest

from audit.recorder import AuditRecorder
from domain.ids import HarnessName
from harness.models import UsageRow, UsageWindow, UsageWindowScope
from inference import DefaultModelFactory, ModelPromptRequest, ModelUnavailableError
from inference.default import CODEX_EXECUTABLE_VARIABLE, INTERNAL_MODEL_VARIABLE
from terminal.models import ScreenReadRequest, ScreenReadResponse, TabCloseRequest, TabCloseResponse
from terminal.models import TabOpenRequest, TabOpenResponse
from terminal.models.values import WindowId
from tests.fake_terminal import FakeTerminal, window


class Usage:
    def __init__(self, rows: tuple[UsageRow, ...] = ()) -> None:
        self.rows = rows

    def usage_rows(self) -> tuple[UsageRow, ...]:
        return self.rows


class Audit:
    def __init__(self) -> None:
        self.errors: list[tuple[str, str, object]] = []

    def error(self, session_or_log="", func="", context=None) -> None:
        self.errors.append((session_or_log, func, context))


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


def factory(
    terminal: InferenceTerminal,
    usage: Usage | None = None,
    audit: Audit | None = None,
    *,
    timeout: float = 1,
) -> DefaultModelFactory:
    return DefaultModelFactory(
        terminal.plugin(),
        usage or Usage(),
        cast(AuditRecorder, audit or Audit()),
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


def test_configured_executable_does_not_depend_on_the_daemon_path() -> None:
    terminal = InferenceTerminal(('{"title":"Configured executable session title"}',))
    model_factory = DefaultModelFactory(
        terminal.plugin(),
        Usage(),
        cast(AuditRecorder, Audit()),
        executable_resolver=lambda name: "/private/model-bin/codex" if name == "codex" else None,
    )

    response = model_factory.small().send(ModelPromptRequest("name this"))

    assert response.text == "Configured executable session title"
    launch = terminal.opened_tabs[0]
    assert launch.command[0] == "/private/model-bin/codex"
    assert launch.environment[0] == (INTERNAL_MODEL_VARIABLE, "1")
    assert launch.environment[1][0] == "PATH"
    assert launch.environment[1][1].startswith("/private/model-bin:")


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
    audit = Audit()
    terminal = InferenceTerminal(
        (
            "rate limit exceeded",
            '{"title":"Fallback provider session title"}',
        )
    )

    response = factory(terminal, audit=audit).small().send(
        ModelPromptRequest("name this", "session-one")
    )

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
    assert audit.errors == []


def test_title_that_violates_the_requested_shape_retries_a_fresh_process() -> None:
    terminal = InferenceTerminal(
        (
            '{"title":"Too short"}',
            '{"title":"Fallback title has enough words"}',
        )
    )

    response = factory(terminal).small().send(ModelPromptRequest("name this"))

    assert response.text == "Fallback title has enough words"
    assert [launch.command[0] for launch in terminal.opened_tabs] == ["codex", "codex"]


def test_transient_provider_failures_retry_the_preferred_provider() -> None:
    terminal = InferenceTerminal(
        (
            "rate limit exceeded",
            "model unavailable",
            '{"title":"Recovered preferred provider title"}',
        )
    )

    response = factory(terminal).small().send(ModelPromptRequest("name this"))

    assert response.text == "Recovered preferred provider title"
    assert [launch.command[0] for launch in terminal.opened_tabs] == [
        "codex",
        "claude",
        "codex",
    ]


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
    audit = Audit()
    terminal = InferenceTerminal(("", "", "", ""), stays_open=True)

    with pytest.raises(ModelUnavailableError):
        factory(terminal, audit=audit, timeout=-1).small().send(
            ModelPromptRequest("name this", "session-one")
        )

    assert terminal.closed_tabs == ["model-1", "model-2", "model-3", "model-4"]
    assert len(audit.errors) == 1
    assert audit.errors[0][1] == "small model (unavailable)"


def test_exhausted_known_quotas_do_not_open_any_provider() -> None:
    audit = Audit()
    terminal = InferenceTerminal(())
    exhausted = Usage(
        (
            usage_row(HarnessName.CODEX, Decimal(100)),
            usage_row(HarnessName.CLAUDE_CODE, Decimal(100)),
        )
    )

    with pytest.raises(ModelUnavailableError):
        factory(terminal, exhausted, audit).small().send(
            ModelPromptRequest("name this", "session-one")
        )

    assert terminal.opened_tabs == []
    assert len(audit.errors) == 1
    assert audit.errors[0][1] == "small model (unavailable)"


def test_missing_executables_report_the_configuration_names() -> None:
    audit = Audit()
    terminal = InferenceTerminal(())
    model_factory = DefaultModelFactory(
        terminal.plugin(),
        Usage(),
        cast(AuditRecorder, audit),
        executable_resolver=lambda _name: None,
    )

    with pytest.raises(ModelUnavailableError):
        model_factory.small().send(ModelPromptRequest("name this", "session-one"))

    assert terminal.opened_tabs == []
    context = cast(dict[str, object], audit.errors[0][2])
    providers = cast(list[dict[str, str]], context["providers"])
    assert providers[0] == {
        "provider": "codex",
        "status": "executable unavailable",
        "configuration": CODEX_EXECUTABLE_VARIABLE,
    }
