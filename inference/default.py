"""Availability-aware one-shot inference through a private terminal plugin."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter, ValidationError
from audit.recorder import AuditRecorder
from domain.ids import HarnessName
from harness.models import UsageRow
from inference.contract import Model, ModelPromptRequest, ModelPromptResponse
from inference.errors import ModelUnavailableError, ProviderUnavailableError
from terminal.contract import TerminalPlugin
from terminal.models import ScreenReadRequest, TabCloseRequest, TabOpenRequest

INTERNAL_MODEL_VARIABLE = "BAQYLAU_INTERNAL_MODEL"
DEFAULT_TIMEOUT_SECONDS = 45.0
PROVIDER_ATTEMPT_TIMEOUT_SECONDS = 15.0
POLL_SECONDS = 0.05
TITLE_SCHEMA_JSON = (
    '{"type":"object","properties":{"title":{"type":"string"}},'
    '"required":["title"],"additionalProperties":false}'
)
UNAVAILABLE_MARKERS = (
    "rate limit",
    "rate_limit",
    "usage limit",
    "quota",
    "model is not available",
    "model unavailable",
    "authentication_error",
    "not logged in",
)
ESCAPED_TITLE = re.compile(r'\\?"title\\?"\s*:\s*\\?"((?:\\.|[^"\\])*)\\?"')
MINIMUM_TITLE_WORDS = 3
AUDIT_OUTPUT_LIMIT = 2_000


class _TitleDocument(BaseModel):
    model_config = ConfigDict(extra="allow")

    title: str


class _CodexItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    text: str | None = None


class _CodexEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    item: _CodexItem | None = None


class _ClaudeOutput(BaseModel):
    model_config = ConfigDict(extra="allow")

    structured_output: _TitleDocument | None = None
    result: str | None = None


class UsageReader(Protocol):
    def usage_rows(self) -> tuple[UsageRow, ...]: ...


@dataclass(frozen=True)
class _Candidate:
    harness: HarnessName
    executable: str
    command: Callable[[str, str], tuple[str, ...]]


def _codex_command(prompt: str, schema_path: str) -> tuple[str, ...]:
    return (
        "codex",
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--model",
        "gpt-5.6-luna",
        "--config",
        'model_reasoning_effort="low"',
        "--sandbox",
        "read-only",
        "--output-schema",
        schema_path,
        "--color",
        "never",
        "--json",
        prompt,
    )


def _claude_command(prompt: str, _schema_path: str) -> tuple[str, ...]:
    return (
        "claude",
        "--print",
        "--safe-mode",
        "--no-session-persistence",
        "--tools",
        "",
        "--model",
        "haiku",
        "--effort",
        "low",
        "--output-format",
        "json",
        "--json-schema",
        TITLE_SCHEMA_JSON,
        prompt,
    )


CANDIDATES = (
    _Candidate(HarnessName.CODEX, "codex", _codex_command),
    _Candidate(HarnessName.CLAUDE_CODE, "claude", _claude_command),
)


class DefaultModelFactory:
    def __init__(
        self,
        terminal_plugin: TerminalPlugin,
        usage_reader: UsageReader,
        audit_recorder: AuditRecorder,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        executable_available: Callable[[str], bool] | None = None,
    ) -> None:
        self.terminal = terminal_plugin
        self.usage = usage_reader
        self.audit = audit_recorder
        self.timeout_seconds = timeout_seconds
        self.executable_available = executable_available or (lambda name: shutil.which(name) is not None)

    def big(self) -> Model:
        raise NotImplementedError

    def mid(self) -> Model:
        raise NotImplementedError

    def small(self) -> Model:
        return _SmallModel(
            self.terminal,
            self.usage,
            self.audit,
            self.timeout_seconds,
            self.executable_available,
        )


class _SmallModel:
    def __init__(
        self,
        terminal_plugin: TerminalPlugin,
        usage_reader: UsageReader,
        audit_recorder: AuditRecorder,
        timeout_seconds: float,
        executable_available: Callable[[str], bool],
    ) -> None:
        self.terminal = terminal_plugin
        self.usage = usage_reader
        self.audit = audit_recorder
        self.timeout_seconds = timeout_seconds
        self.executable_available = executable_available

    def send(self, model_prompt_request: ModelPromptRequest) -> ModelPromptResponse:
        try:
            candidates, provider_states = self._candidates()
        except Exception as error:
            self.audit.error(
                model_prompt_request.session_id,
                "small model (provider selection)",
                _error_context(error),
            )
            raise
        failures: list[str] = []
        # A CLI or remote request can fail transiently even though account
        # capacity remains. Try every provider once, then retry the preferred
        # one in a fresh ephemeral process. Per-attempt time is capped so the
        # extra recovery attempt is still faster than one old 45-second hang.
        attempts = (*candidates, candidates[0]) if candidates else ()
        for attempt, candidate in enumerate(attempts, start=1):
            try:
                return self._send(candidate, model_prompt_request)
            except ProviderUnavailableError as error:
                failures.append(f"{candidate.harness}: {error}")
                context: dict[str, JsonValue] = {
                    **_error_context(error),
                    "provider": str(candidate.harness),
                    "attempt": attempt,
                    "stage": error.stage,
                }
                if error.output:
                    context["output"] = error.output[:AUDIT_OUTPUT_LIMIT]
                self.audit.error(
                    model_prompt_request.session_id,
                    "small model (provider attempt)",
                    context,
                )
            except Exception as error:
                self.audit.error(
                    model_prompt_request.session_id,
                    "small model (provider attempt)",
                    {
                        **_error_context(error),
                        "provider": str(candidate.harness),
                        "attempt": attempt,
                    },
                )
                raise
        reason = "; ".join(failures) if failures else "no provider is available"
        try:
            raise ModelUnavailableError(reason)
        except ModelUnavailableError as error:
            failure_values: list[JsonValue] = [failure for failure in failures]
            self.audit.error(
                model_prompt_request.session_id,
                "small model (unavailable)",
                {
                    **_error_context(error),
                    "providers": list(provider_states),
                    "attempt_failures": failure_values,
                },
            )
            raise

    def _candidates(self) -> tuple[tuple[_Candidate, ...], tuple[JsonValue, ...]]:
        rows = self.usage.usage_rows()
        ranked: list[tuple[Decimal, int, _Candidate]] = []
        states: list[JsonValue] = []
        for order, candidate in enumerate(CANDIDATES):
            if not self.executable_available(candidate.executable):
                states.append(
                    {
                        "provider": str(candidate.harness),
                        "status": "executable unavailable",
                    }
                )
                continue
            capacity = _remaining_capacity(candidate.harness, rows)
            if capacity <= 0:
                matching = tuple(row for row in rows if row.harness == candidate.harness)
                status = (
                    "authentication error"
                    if any(row.authentication_error for row in matching)
                    else "capacity exhausted"
                )
                states.append(
                    {
                        "provider": str(candidate.harness),
                        "status": status,
                        "remaining_capacity_percent": str(capacity),
                    }
                )
                continue
            states.append(
                {
                    "provider": str(candidate.harness),
                    "status": "available",
                    "remaining_capacity_percent": str(capacity),
                }
            )
            ranked.append((capacity, -order, candidate))
        ranked.sort(reverse=True, key=lambda item: (item[0], item[1]))
        return tuple(candidate for _capacity, _order, candidate in ranked), tuple(states)

    def _send(self, candidate: _Candidate, request: ModelPromptRequest) -> ModelPromptResponse:
        with tempfile.TemporaryDirectory(prefix="baqylau-model-") as directory:
            schema_path = os.path.join(directory, "title-schema.json")
            with open(schema_path, "w", encoding="utf-8") as schema_file:
                schema_file.write(TITLE_SCHEMA_JSON)
            opened = self.terminal.tabs.open_tab(
                TabOpenRequest(
                    working_directory=directory,
                    command=candidate.command(request.prompt, schema_path),
                    title="Baqylau internal model",
                    environment=((INTERNAL_MODEL_VARIABLE, "1"),),
                )
            )
            if not opened.succeeded or opened.window_id is None:
                raise ProviderUnavailableError(
                    opened.reason or "model process did not start",
                    stage="start",
                )
            window_id = opened.window_id
            try:
                deadline = time.monotonic() + min(
                    self.timeout_seconds,
                    PROVIDER_ATTEMPT_TIMEOUT_SECONDS,
                )
                while any(window.window_id == window_id for window in self.terminal.metadata.windows()):
                    if time.monotonic() >= deadline:
                        raise ProviderUnavailableError(
                            "model response timed out",
                            stage="wait",
                        )
                    time.sleep(POLL_SECONDS)
                # Let the terminal's drain thread consume the final bytes after
                # the process disappears from metadata.
                time.sleep(POLL_SECONDS)
                screen = self.terminal.viewport.read_screen(ScreenReadRequest(window_id))
                if not screen.succeeded or screen.text is None:
                    raise ProviderUnavailableError(
                        screen.reason or "model output was not readable",
                        stage="read output",
                    )
                try:
                    return ModelPromptResponse(_title_from_output(screen.text))
                except ProviderUnavailableError as error:
                    raise ProviderUnavailableError(
                        str(error),
                        stage="parse output",
                        output=screen.text,
                    ) from error
            finally:
                self.terminal.tabs.close_tab(TabCloseRequest(window_id))


def _error_context(error: Exception) -> dict[str, JsonValue]:
    return {
        "error_type": type(error).__name__,
        "error": str(error),
    }


def _remaining_capacity(harness: HarnessName, rows: tuple[UsageRow, ...]) -> Decimal:
    matching = tuple(row for row in rows if row.harness == harness)
    if any(row.authentication_error for row in matching):
        return Decimal(0)
    windows = tuple(window for row in matching for window in row.windows)
    if not windows:
        return Decimal(100)
    return max(Decimal(0), min(Decimal(100) - window.used_percent for window in windows))


def _title_from_output(output: str) -> str:
    invalid_shape = False
    candidates = (*reversed(output.splitlines()), "".join(output.splitlines()))
    for candidate in candidates:
        title = _title_from_document(candidate)
        if title:
            if _has_requested_title_shape(title):
                return title
            invalid_shape = True
    match = ESCAPED_TITLE.search(output)
    if match:
        try:
            title = TypeAdapter(str).validate_json(f'"{match.group(1)}"')
            if _has_requested_title_shape(title):
                return title
            invalid_shape = True
        except ValidationError:
            pass
    lowered = output.lower()
    if any(marker in lowered for marker in UNAVAILABLE_MARKERS):
        raise ProviderUnavailableError("provider reported an availability limit")
    if invalid_shape:
        raise ProviderUnavailableError("model returned a title outside the requested shape")
    raise ProviderUnavailableError("model returned no structured title")


def _has_requested_title_shape(title: str) -> bool:
    return len(title.split()) >= MINIMUM_TITLE_WORDS


def _title_from_document(value: str) -> str | None:
    try:
        direct = _TitleDocument.model_validate_json(value)
        if direct.title.strip():
            return direct.title
    except ValidationError:
        pass
    try:
        codex = _CodexEvent.model_validate_json(value)
        if codex.item and codex.item.text:
            nested = _TitleDocument.model_validate_json(codex.item.text)
            if nested.title.strip():
                return nested.title
    except ValidationError:
        pass
    try:
        claude = _ClaudeOutput.model_validate_json(value)
        if claude.structured_output and claude.structured_output.title.strip():
            return claude.structured_output.title
        if claude.result:
            nested = _TitleDocument.model_validate_json(claude.result)
            if nested.title.strip():
                return nested.title
    except ValidationError:
        pass
    return None
