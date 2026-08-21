"""Read Codex account rate limits from its app server."""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, ValidationError

REQUEST_TIMEOUT_SECONDS = 6.0
CACHE_SECONDS = 120.0
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


class RateLimitsResult(BaseModel):
    model_config = _RATE_LIMITS_FOREIGN
    primary: RateLimitWindowResult | None = None
    secondary: RateLimitWindowResult | None = None
    planType: str | None = None


class AccountRateLimitsResponse(BaseModel):
    model_config = _RATE_LIMITS_FOREIGN
    rateLimits: RateLimitsResult | None = None


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


def subprocess_environment() -> dict[str, str]:
    environment = dict(os.environ)
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
        initialize = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"clientInfo": {"name": "baqylau", "version": "1"}},
        }
        request = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "account/rateLimits/read",
            "params": {},
        }
        process.stdin.write(json.dumps(initialize) + "\n")
        process.stdin.write(json.dumps(request) + "\n")
        process.stdin.flush()
        deadline = time.time() + REQUEST_TIMEOUT_SECONDS
        while time.time() < deadline:
            line = process.stdout.readline()
            if not line:
                return None
            try:
                response = json.loads(line)
            except json.JSONDecodeError:
                continue
            if response.get("id") == 2:
                result = response.get("result")
                if not isinstance(result, dict):
                    return None
                try:
                    return AccountRateLimitsResponse.model_validate(result)
                except ValidationError:
                    return None
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
    _cached_rate_limits = (now + CACHE_SECONDS, result)
    return result
