"""Read Claude plan limits through Claude Code's structured usage request.

Claude Code owns its OAuth credentials. Its request path refreshes an expired
access token under Claude's cross-process lock, calls the usage API, and retries
after an authentication response. Baqylau does not read or write those tokens.

The response is a live foreign document. Fields that baqylau reads stay typed.
New fields that baqylau does not read do not break usage refresh.
"""

from __future__ import annotations

import os
import select
import subprocess
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from domain.ids import HarnessName
from harness.impl.claude_code.ids import ClaudeCodeControlRequestId
from harness.models import UsageWindowSample
from harness.runtime import HarnessRuntimeConfig, default_harness_runtime_configs

_OWNED = ConfigDict(extra="forbid", frozen=True)
_FOREIGN = ConfigDict(extra="ignore", frozen=True)


class LiveUsageWindow(BaseModel):
    model_config = _FOREIGN
    utilization: float | int | None = None
    resets_at: str | None = None


class LiveModelScopedWindow(BaseModel):
    model_config = _FOREIGN
    display_name: str | None = None
    utilization: float | int | None = None
    resets_at: str | None = None


class LiveLimitModel(BaseModel):
    model_config = _FOREIGN
    display_name: str


class LiveLimitScope(BaseModel):
    model_config = _FOREIGN
    model: LiveLimitModel | None = None


class LiveLimit(BaseModel):
    model_config = _FOREIGN
    kind: str
    percent: float | int
    resets_at: str | None = None
    scope: LiveLimitScope | None = None


class LiveRateLimits(BaseModel):
    model_config = _FOREIGN
    five_hour: LiveUsageWindow | None = None
    seven_day: LiveUsageWindow | None = None
    model_scoped: tuple[LiveModelScopedWindow, ...] | None = None
    limits: tuple[LiveLimit, ...] = ()


class GetUsageResponse(BaseModel):
    model_config = _FOREIGN
    rate_limits: LiveRateLimits | None = None
    rate_limits_available: bool
    subscription_type: str | None = None


class GetUsageRequest(BaseModel):
    model_config = _OWNED
    subtype: Literal["get_usage"] = "get_usage"


class ControlRequestLine(BaseModel):
    model_config = _OWNED
    type: Literal["control_request"] = "control_request"
    request_id: ClaudeCodeControlRequestId
    request: GetUsageRequest


class ControlResponseBody(BaseModel):
    model_config = _FOREIGN
    subtype: Literal["success"]
    request_id: ClaudeCodeControlRequestId
    response: GetUsageResponse | None = None


class ControlResponseLine(BaseModel):
    model_config = _FOREIGN
    type: Literal["control_response"]
    response: ControlResponseBody


class ControlResponseIdentityBody(BaseModel):
    model_config = _FOREIGN
    request_id: ClaudeCodeControlRequestId | None = None


class ControlResponseIdentity(BaseModel):
    model_config = _FOREIGN
    type: str | None = None
    response: ControlResponseIdentityBody | None = None


PROBE_TIMEOUT_SECONDS = 6.0
CACHE_SECONDS = 120.0
RETRY_SECONDS = 5.0
STALE_SECONDS = 300.0
PERMANENT_FAILURE_SECONDS = 60.0
REQUEST_ID = ClaudeCodeControlRequestId("baqylau-usage")
REQUEST = ControlRequestLine(request_id=REQUEST_ID, request=GetUsageRequest())

DISCARDED_VARIABLES = (
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CONFIG_DIR",
    "CLAUDE_SUBSCRIPTION_SLUG",
    "CLAUDE_SUBSCRIPTION_LABEL",
)
CONFIG_DIRECTORY_VARIABLE = "CLAUDE_CONFIG_DIR"
PROBE_VARIABLE = "BAQYLAU_USAGE_PROBE"

MODEL_WINDOW_PREFIX = "seven_day_"
ACCOUNT_WINDOWS = ("five_hour", "seven_day")
MAX_MODEL_WINDOWS = 6


@dataclass(frozen=True)
class LiveUsage:
    captured_at: float
    plan: str | None
    windows: tuple[UsageWindowSample, ...]


@dataclass(frozen=True)
class LiveUsageCollection:
    """The last authoritative usage and one permanent failure, if present."""

    usage: LiveUsage | None
    error: str | None


@dataclass(frozen=True)
class ProbeFailure:
    message: str
    recoverable: bool


@dataclass(frozen=True)
class ProbeResult:
    response: GetUsageResponse | None
    failure: ProbeFailure | None


@dataclass(frozen=True)
class CacheEntry:
    runtime_key: str
    expires_at: float
    collection: LiveUsageCollection
    last_good: LiveUsage | None


_cache: list[CacheEntry] = []
_cache_lock = threading.Lock()


def _default_runtime_config() -> HarnessRuntimeConfig:
    return default_harness_runtime_configs().for_harness(HarnessName.CLAUDE_CODE)


def subprocess_environment(
    harness_runtime_config: HarnessRuntimeConfig,
) -> Mapping[str, str]:
    environment = os.environ.copy()
    for name in DISCARDED_VARIABLES:
        environment.pop(name, None)
    if not harness_runtime_config.use_vendor_default_configuration:
        environment[CONFIG_DIRECTORY_VARIABLE] = str(
            harness_runtime_config.configuration_directory
        )
    environment[PROBE_VARIABLE] = "1"
    return environment


def _epoch_seconds(value: str | None) -> float | None:
    if not value or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def _percent(value: float | int | None) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    return Decimal(max(0, min(100, int(round(value)))))


def _model_key(display_name: str | None) -> str | None:
    if not display_name:
        return None
    slug = "".join(
        character if character.isalnum() else "_"
        for character in display_name.lower()
    )
    slug = "_".join(part for part in slug.split("_") if part)
    if not slug or not slug.isascii() or len(slug) > 24:
        return None
    return MODEL_WINDOW_PREFIX + slug


def windows(live_rate_limits: LiveRateLimits | None) -> tuple[UsageWindowSample, ...]:
    if live_rate_limits is None:
        return ()
    samples: dict[str, UsageWindowSample] = {}
    for key, window in (
        ("five_hour", live_rate_limits.five_hour),
        ("seven_day", live_rate_limits.seven_day),
    ):
        if window is None:
            continue
        used_percent = _percent(window.utilization)
        if used_percent is None:
            continue
        samples[key] = UsageWindowSample(
            key,
            used_percent,
            _epoch_seconds(window.resets_at),
        )
    for bucket in live_rate_limits.model_scoped or ():
        if len(samples) >= len(ACCOUNT_WINDOWS) + MAX_MODEL_WINDOWS:
            break
        model_key = _model_key(bucket.display_name)
        used_percent = _percent(bucket.utilization)
        if model_key is None or used_percent is None:
            continue
        samples[model_key] = UsageWindowSample(
            model_key,
            used_percent,
            _epoch_seconds(bucket.resets_at),
        )
    for limit in live_rate_limits.limits:
        if len(samples) >= len(ACCOUNT_WINDOWS) + MAX_MODEL_WINDOWS:
            break
        model = limit.scope.model if limit.scope is not None else None
        if model is None or "weekly" not in limit.kind.lower():
            continue
        model_key = _model_key(model.display_name)
        used_percent = _percent(limit.percent)
        if model_key is None or used_percent is None:
            continue
        samples[model_key] = UsageWindowSample(
            model_key,
            used_percent,
            _epoch_seconds(limit.resets_at),
        )
    return tuple(samples.values())


def _control_response(
    process: subprocess.Popen[str],
    deadline: float,
) -> ProbeResult:
    if process.stdout is None:
        return ProbeResult(None, ProbeFailure("Claude usage probe has no output", True))
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return ProbeResult(None, ProbeFailure("Claude usage probe timed out", True))
        readable, _, _ = select.select((process.stdout,), (), (), remaining)
        if not readable:
            return ProbeResult(None, ProbeFailure("Claude usage probe timed out", True))
        line = process.stdout.readline()
        if not line:
            return ProbeResult(None, ProbeFailure("Claude usage probe ended early", True))
        try:
            identity = ControlResponseIdentity.model_validate_json(line)
        except ValidationError:
            continue
        if identity.type != "control_response" or identity.response is None:
            continue
        if identity.response.request_id != REQUEST_ID:
            continue
        try:
            message = ControlResponseLine.model_validate_json(line)
        except ValidationError as error:
            location = ".".join(str(part) for part in error.errors()[0]["loc"])
            return ProbeResult(
                None,
                ProbeFailure(
                    f"Claude usage response is incompatible at {location}",
                    False,
                ),
            )
        document = message.response.response
        if document is None:
            return ProbeResult(None, ProbeFailure("Claude returned no usage data", True))
        return ProbeResult(document, None)


def _stop(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=1.0)


def request_usage(harness_runtime_config: HarnessRuntimeConfig) -> ProbeResult:
    try:
        process = subprocess.Popen(
            [
                harness_runtime_config.executable,
                "--print",
                "--verbose",
                "--input-format",
                "stream-json",
                "--output-format",
                "stream-json",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env=subprocess_environment(harness_runtime_config),
            cwd=os.path.expanduser("~"),
            start_new_session=True,
        )
    except FileNotFoundError:
        return ProbeResult(None, ProbeFailure("Claude Code is not installed", False))
    except OSError:
        return ProbeResult(None, ProbeFailure("Claude usage probe could not start", True))
    try:
        if process.stdin is None:
            return ProbeResult(None, ProbeFailure("Claude usage probe has no input", True))
        process.stdin.write(REQUEST.model_dump_json() + "\n")
        process.stdin.flush()
        return _control_response(process, time.monotonic() + PROBE_TIMEOUT_SECONDS)
    except OSError:
        return ProbeResult(None, ProbeFailure("Claude usage probe communication failed", True))
    finally:
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        _stop(process)


def collect(
    harness_runtime_config: HarnessRuntimeConfig | None = None,
) -> LiveUsageCollection:
    """Return fresh usage, or the last good result during a recoverable retry."""
    runtime_config = harness_runtime_config or _default_runtime_config()
    with _cache_lock:
        return _collect(runtime_config)


def _collect(harness_runtime_config: HarnessRuntimeConfig) -> LiveUsageCollection:
    now = time.time()
    cache_key = (
        f"{harness_runtime_config.executable}\0"
        f"{harness_runtime_config.configuration_directory}"
    )
    cached = next((entry for entry in _cache if entry.runtime_key == cache_key), None)
    if cached is not None and cached.expires_at > now:
        return cached.collection

    last_good = cached.last_good if cached is not None else None
    probe = request_usage(harness_runtime_config)
    document = probe.response
    if document is not None and document.rate_limits_available:
        samples = windows(document.rate_limits)
        if samples:
            usage = LiveUsage(
                captured_at=now,
                plan=document.subscription_type or None,
                windows=samples,
            )
            collection = LiveUsageCollection(usage, None)
            last_good = usage
            ttl = CACHE_SECONDS
        else:
            collection = LiveUsageCollection(
                None,
                "Claude usage response contains no limit windows",
            )
            last_good = None
            ttl = PERMANENT_FAILURE_SECONDS
    elif document is not None:
        collection = LiveUsageCollection(
            None,
            "Claude plan usage is not available for this account",
        )
        last_good = None
        ttl = PERMANENT_FAILURE_SECONDS
    elif probe.failure is not None and probe.failure.recoverable:
        if last_good is not None and now - last_good.captured_at > STALE_SECONDS:
            last_good = None
        collection = LiveUsageCollection(last_good, None)
        ttl = RETRY_SECONDS
    else:
        message = probe.failure.message if probe.failure is not None else "Claude usage failed"
        collection = LiveUsageCollection(None, message)
        last_good = None
        ttl = PERMANENT_FAILURE_SECONDS

    _cache[:] = [entry for entry in _cache if entry.runtime_key != cache_key]
    _cache.append(CacheEntry(cache_key, now + ttl, collection, last_good))
    return collection
