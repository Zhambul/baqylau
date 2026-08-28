"""Read Codex plan limits through the Codex app server."""

from __future__ import annotations

import os
import select
import subprocess
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from domain.ids import HarnessName
from harness.runtime import HarnessRuntimeConfig, default_harness_runtime_configs

REQUEST_TIMEOUT_SECONDS = 6.0
CACHE_SECONDS = 120.0
RETRY_SECONDS = 2.0
STALE_SECONDS = 300.0
PERMANENT_FAILURE_SECONDS = 60.0
_OWNED = ConfigDict(extra="forbid", frozen=True)
_FOREIGN = ConfigDict(extra="ignore", frozen=True)


class RateLimitWindowResult(BaseModel):
    model_config = _FOREIGN
    usedPercent: float | int | None = None
    windowDurationMins: float | int | None = None
    resetsAt: float | int | None = None


class RateLimitCredits(BaseModel):
    model_config = _FOREIGN
    hasCredits: bool
    unlimited: bool
    balance: str


class RateLimitsResult(BaseModel):
    model_config = _FOREIGN
    limitId: str | None = None
    limitName: str | None = None
    primary: RateLimitWindowResult | None = None
    secondary: RateLimitWindowResult | None = None
    credits: RateLimitCredits | None = None
    individualLimit: None = None
    spendControlReached: bool | None = None
    planType: str | None = None
    rateLimitReachedType: str | None = None


class AccountRateLimitsResponse(BaseModel):
    model_config = _FOREIGN
    rateLimits: RateLimitsResult | None = None


class ClientInfo(BaseModel):
    model_config = _OWNED
    name: str
    version: str


class InitializeParams(BaseModel):
    model_config = _OWNED
    clientInfo: ClientInfo


class EmptyParams(BaseModel):
    model_config = _OWNED


class InitializeRequest(BaseModel):
    model_config = _OWNED
    jsonrpc: Literal["2.0"] = "2.0"
    id: Literal[1] = 1
    method: Literal["initialize"] = "initialize"
    params: InitializeParams


class RateLimitsRequest(BaseModel):
    model_config = _OWNED
    jsonrpc: Literal["2.0"] = "2.0"
    id: Literal[2] = 2
    method: Literal["account/rateLimits/read"] = "account/rateLimits/read"
    params: EmptyParams


class RpcResponseHeader(BaseModel):
    model_config = _FOREIGN
    id: int | None = None


class RateLimitsRpcResponse(BaseModel):
    model_config = _FOREIGN
    jsonrpc: Literal["2.0"] = "2.0"
    id: Literal[2]
    result: AccountRateLimitsResponse


class RpcErrorBody(BaseModel):
    model_config = _FOREIGN
    code: int
    message: str


class RateLimitsRpcError(BaseModel):
    model_config = _FOREIGN
    jsonrpc: Literal["2.0"] = "2.0"
    id: Literal[2]
    error: RpcErrorBody


@dataclass(frozen=True, kw_only=True)
class NormalizedRateLimitWindow:
    used_percent: float | int
    duration_minutes: int
    resets_at: float | int | None


@dataclass(frozen=True, kw_only=True)
class NormalizedRateLimits:
    plan: str
    windows: tuple[NormalizedRateLimitWindow, ...]


@dataclass(frozen=True)
class ProbeFailure:
    message: str
    recoverable: bool


@dataclass(frozen=True)
class ProbeResult:
    response: AccountRateLimitsResponse | None
    failure: ProbeFailure | None


@dataclass(frozen=True)
class RateLimitsCollection:
    usage: NormalizedRateLimits | None
    error: str | None


@dataclass(frozen=True)
class CacheEntry:
    runtime_key: str
    expires_at: float
    collection: RateLimitsCollection
    last_good: NormalizedRateLimits | None
    last_good_at: float | None


_cached_rate_limits: CacheEntry | None = None
_cache_lock = threading.Lock()


def _default_runtime_config() -> HarnessRuntimeConfig:
    return default_harness_runtime_configs().for_harness(HarnessName.CODEX)


def subprocess_environment(
    harness_runtime_config: HarnessRuntimeConfig,
) -> Mapping[str, str]:
    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(
        harness_runtime_config.configuration_directory
    )
    return environment


def _permanent_rpc_error(message: str) -> bool:
    normalized = message.lower()
    return any(
        marker in normalized
        for marker in (
            "auth",
            "forbidden",
            "login",
            "revoked",
            "token expired",
            "token reused",
            "unauthorized",
        )
    )


def _response(process: subprocess.Popen[str], deadline: float) -> ProbeResult:
    if process.stdout is None:
        return ProbeResult(None, ProbeFailure("Codex app server has no output", True))
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return ProbeResult(None, ProbeFailure("Codex usage request timed out", True))
        readable, _, _ = select.select((process.stdout,), (), (), remaining)
        if not readable:
            return ProbeResult(None, ProbeFailure("Codex usage request timed out", True))
        line = process.stdout.readline()
        if not line:
            return ProbeResult(None, ProbeFailure("Codex app server ended early", True))
        try:
            header = RpcResponseHeader.model_validate_json(line)
        except ValidationError:
            continue
        if header.id != 2:
            continue
        try:
            response = RateLimitsRpcResponse.model_validate_json(line)
        except ValidationError as success_error:
            try:
                error = RateLimitsRpcError.model_validate_json(line).error
            except ValidationError:
                location = ".".join(
                    str(part) for part in success_error.errors()[0]["loc"]
                )
                return ProbeResult(
                    None,
                    ProbeFailure(
                        f"Codex usage response is incompatible at {location}",
                        False,
                    ),
                )
            return ProbeResult(
                None,
                ProbeFailure(error.message, not _permanent_rpc_error(error.message)),
            )
        return ProbeResult(response.result, None)


def _stop(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=1.0)


def request_rate_limits(harness_runtime_config: HarnessRuntimeConfig) -> ProbeResult:
    try:
        process = subprocess.Popen(
            [harness_runtime_config.executable, "app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env=subprocess_environment(harness_runtime_config),
        )
    except FileNotFoundError:
        return ProbeResult(None, ProbeFailure("Codex is not installed", False))
    except OSError:
        return ProbeResult(None, ProbeFailure("Codex app server could not start", True))
    try:
        if process.stdin is None:
            return ProbeResult(None, ProbeFailure("Codex app server has no input", True))
        initialize = InitializeRequest(
            params=InitializeParams(clientInfo=ClientInfo(name="baqylau", version="1"))
        )
        request = RateLimitsRequest(params=EmptyParams())
        process.stdin.write(initialize.model_dump_json() + "\n")
        process.stdin.write(request.model_dump_json() + "\n")
        process.stdin.flush()
        return _response(process, time.monotonic() + REQUEST_TIMEOUT_SECONDS)
    except OSError:
        return ProbeResult(None, ProbeFailure("Codex usage request failed", True))
    finally:
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        _stop(process)


def normalize_rate_limits(
    account_rate_limits_response: AccountRateLimitsResponse | None,
) -> NormalizedRateLimits | None:
    rate_limits = (
        account_rate_limits_response.rateLimits
        if account_rate_limits_response is not None
        else None
    )
    if rate_limits is None:
        return None
    windows = []
    for window in (rate_limits.primary, rate_limits.secondary):
        if window is None:
            continue
        used_percent = window.usedPercent
        duration_minutes = window.windowDurationMins
        if used_percent is None or duration_minutes is None:
            continue
        windows.append(
            NormalizedRateLimitWindow(
                used_percent=used_percent,
                duration_minutes=int(duration_minutes),
                resets_at=window.resetsAt,
            )
        )
    if not windows:
        return None
    return NormalizedRateLimits(
        plan=rate_limits.planType or "",
        windows=tuple(windows),
    )


def collect_rate_limits(
    harness_runtime_config: HarnessRuntimeConfig | None = None,
) -> RateLimitsCollection:
    """Return fresh limits, or the last good result during a recoverable retry."""
    runtime_config = harness_runtime_config or _default_runtime_config()
    with _cache_lock:
        return _collect_rate_limits(runtime_config)


def _collect_rate_limits(
    harness_runtime_config: HarnessRuntimeConfig,
) -> RateLimitsCollection:
    global _cached_rate_limits
    now = time.time()
    runtime_key = (
        f"{harness_runtime_config.executable}\0"
        f"{harness_runtime_config.configuration_directory}"
    )
    if (
        _cached_rate_limits is not None
        and _cached_rate_limits.runtime_key == runtime_key
        and _cached_rate_limits.expires_at > now
    ):
        return _cached_rate_limits.collection

    last_good = (
        _cached_rate_limits.last_good
        if _cached_rate_limits is not None
        and _cached_rate_limits.runtime_key == runtime_key
        else None
    )
    last_good_at = (
        _cached_rate_limits.last_good_at
        if _cached_rate_limits is not None
        and _cached_rate_limits.runtime_key == runtime_key
        else None
    )
    probe = request_rate_limits(harness_runtime_config)
    usage = normalize_rate_limits(probe.response)
    if usage is not None:
        collection = RateLimitsCollection(usage, None)
        last_good = usage
        last_good_at = now
        ttl = CACHE_SECONDS
    elif probe.response is not None:
        collection = RateLimitsCollection(
            None,
            "Codex usage response contains no limit windows",
        )
        last_good = None
        last_good_at = None
        ttl = PERMANENT_FAILURE_SECONDS
    elif probe.failure is not None and probe.failure.recoverable:
        if last_good_at is None or now - last_good_at > STALE_SECONDS:
            last_good = None
            last_good_at = None
        collection = RateLimitsCollection(last_good, None)
        ttl = RETRY_SECONDS
    else:
        message = probe.failure.message if probe.failure is not None else "Codex usage failed"
        collection = RateLimitsCollection(None, message)
        last_good = None
        last_good_at = None
        ttl = PERMANENT_FAILURE_SECONDS

    _cached_rate_limits = CacheEntry(
        runtime_key,
        now + ttl,
        collection,
        last_good,
        last_good_at,
    )
    return collection
