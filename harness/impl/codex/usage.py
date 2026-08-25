"""Read Codex account rate limits from its app server."""

from __future__ import annotations

import os
import select
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

REQUEST_TIMEOUT_SECONDS = 6.0
CACHE_SECONDS = 120.0
FAILED_CACHE_SECONDS = 2.0
BINARY_DIRECTORIES = (
    "~/.hermes/node/bin",
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "~/.local/bin",
)

# The app-server's JSON-RPC response to `account/rateLimits/read` — a
# DIFFERENT foreign source from the rollout file (canonical/records.py owns
# that one): a live subprocess reply, not a stored document, so a shape
# mismatch here degrades to "no usage row" (read_rate_limits returns None)
# rather than a stored `translation_failed` — there is nothing recorded to
# fail. `extra="forbid"` still applies: an unrecognised field is exactly as
# much a sign the app-server's contract moved as a rollout field would be.
_RATE_LIMITS_FOREIGN = ConfigDict(extra="forbid", frozen=True)


class RateLimitWindowResult(BaseModel):
    model_config = _RATE_LIMITS_FOREIGN
    usedPercent: float | int | None = None
    windowDurationMins: float | int | None = None
    resetsAt: float | int | None = None


class RateLimitCredits(BaseModel):
    model_config = _RATE_LIMITS_FOREIGN
    hasCredits: bool
    unlimited: bool
    balance: str


class RateLimitsResult(BaseModel):
    model_config = _RATE_LIMITS_FOREIGN
    limitId: str | None = None
    limitName: str | None = None
    primary: RateLimitWindowResult | None = None
    secondary: RateLimitWindowResult | None = None
    credits: RateLimitCredits | None = None
    individualLimit: None = None
    spendControlReached: bool | None = None
    planType: str | None = None
    rateLimitReachedType: str | None = None


class RateLimitResetCredit(BaseModel):
    model_config = _RATE_LIMITS_FOREIGN
    id: str
    resetType: str
    status: str
    grantedAt: float | int
    expiresAt: float | int
    title: str
    description: str


class RateLimitResetCredits(BaseModel):
    model_config = _RATE_LIMITS_FOREIGN
    availableCount: int
    credits: tuple[RateLimitResetCredit, ...]


class AccountRateLimitsResponse(BaseModel):
    model_config = _RATE_LIMITS_FOREIGN
    rateLimits: RateLimitsResult | None = None
    # Limit ids are runtime/vendor-defined (the base `codex` bucket plus
    # model-specific buckets), so this is genuinely a dynamic keyed mapping.
    rateLimitsByLimitId: Mapping[str, RateLimitsResult]
    rateLimitResetCredits: RateLimitResetCredits | None = None


class ClientInfo(BaseModel):
    model_config = _RATE_LIMITS_FOREIGN
    name: str
    version: str


class InitializeParams(BaseModel):
    model_config = _RATE_LIMITS_FOREIGN
    clientInfo: ClientInfo


class EmptyParams(BaseModel):
    model_config = _RATE_LIMITS_FOREIGN


class InitializeRequest(BaseModel):
    model_config = _RATE_LIMITS_FOREIGN
    jsonrpc: Literal["2.0"] = "2.0"
    id: Literal[1] = 1
    method: Literal["initialize"] = "initialize"
    params: InitializeParams


class RateLimitsRequest(BaseModel):
    model_config = _RATE_LIMITS_FOREIGN
    jsonrpc: Literal["2.0"] = "2.0"
    id: Literal[2] = 2
    method: Literal["account/rateLimits/read"] = "account/rateLimits/read"
    params: EmptyParams


class RpcResponseHeader(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    id: int | None = None


class RateLimitsRpcResponse(BaseModel):
    model_config = _RATE_LIMITS_FOREIGN
    jsonrpc: Literal["2.0"] = "2.0"
    id: Literal[2]
    result: AccountRateLimitsResponse


@dataclass(frozen=True, kw_only=True)
class NormalizedRateLimitWindow:
    used_percent: float | int
    duration_minutes: int
    resets_at: float | int | None


@dataclass(frozen=True, kw_only=True)
class NormalizedRateLimits:
    plan: str
    windows: tuple[NormalizedRateLimitWindow, ...]


_cached_rate_limits: tuple[float, NormalizedRateLimits | None] | None = None


def subprocess_environment() -> Mapping[str, str]:
    environment = os.environ.copy()
    directories = []
    configured_directory = environment.get("CODEX_BIN_DIR")
    if configured_directory and os.path.isdir(configured_directory):
        directories.append(configured_directory)
    for candidate in BINARY_DIRECTORIES:
        directory = os.path.expanduser(candidate)
        if os.path.isdir(directory) and directory not in directories:
            directories.append(directory)
    if directories:
        directories.append(environment.get("PATH", ""))
        environment["PATH"] = os.pathsep.join(directories)
    return environment


def request_rate_limits() -> AccountRateLimitsResponse | None:
    try:
        process = subprocess.Popen(
            ["codex", "app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env=subprocess_environment(),
        )
    except OSError:
        return None
    try:
        if process.stdin is None or process.stdout is None:
            return None
        initialize = InitializeRequest(
            params=InitializeParams(clientInfo=ClientInfo(name="baqylau", version="1"))
        )
        request = RateLimitsRequest(params=EmptyParams())
        process.stdin.write(initialize.model_dump_json() + "\n")
        process.stdin.write(request.model_dump_json() + "\n")
        process.stdin.flush()
        deadline = time.monotonic() + REQUEST_TIMEOUT_SECONDS
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            readable, _, _ = select.select((process.stdout,), (), (), remaining)
            if not readable:
                return None
            line = process.stdout.readline()
            if not line:
                return None
            try:
                header = RpcResponseHeader.model_validate_json(line)
            except ValidationError:
                continue
            if header.id == 2:
                try:
                    return RateLimitsRpcResponse.model_validate_json(line).result
                except ValidationError:
                    return None
    finally:
        if process.stdin is not None:
            process.stdin.close()
        process.terminate()


def normalize_rate_limits(
    account_rate_limits_response: AccountRateLimitsResponse | None,
) -> NormalizedRateLimits | None:
    rate_limits = (
        account_rate_limits_response.rateLimits if account_rate_limits_response is not None else None
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
        windows.append(NormalizedRateLimitWindow(
            used_percent=used_percent,
            duration_minutes=int(duration_minutes),
            resets_at=window.resetsAt,
        ))
    if not windows:
        return None
    return NormalizedRateLimits(plan=rate_limits.planType or "", windows=tuple(windows))


def read_rate_limits() -> NormalizedRateLimits | None:
    global _cached_rate_limits
    now = time.time()
    if _cached_rate_limits is not None and _cached_rate_limits[0] > now:
        return _cached_rate_limits[1]
    result = normalize_rate_limits(request_rate_limits())
    cache_seconds = CACHE_SECONDS if result is not None else FAILED_CACHE_SECONDS
    _cached_rate_limits = (now + cache_seconds, result)
    return result
