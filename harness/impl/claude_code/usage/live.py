"""Ask Claude Code itself how full an account's plan windows are.

WHY THIS EXISTS. The status line carries exactly two windows. Claude Code builds
that payload by hand —

    R = { ...k.five_hour && {five_hour: {...}},
          ...k.seven_day && {seven_day: {...}} }

— out of a rate-limit object that holds far more, so a per-model window (the
weekly Fable cap) can never arrive that way. It exists in one place: the
`/api/oauth/usage` response, which the CLI fetches and keeps to itself.

WHY WE ASK THE CLI AND NOT THE API. That endpoint needs the `user:profile`
scope. The token the account switcher exports is a long-lived one, and those are
inference-only by the vendor's own words ("Long-lived tokens are limited to
inference-only for security reasons"), so calling the endpoint ourselves would
mean using the full-login credentials in the Keychain — which are refreshed
behind rotating refresh tokens the CLI owns. Racing the CLI for its own
credentials to read a number the CLI will hand over on request is a bad trade.

So this is the Codex pattern (`harness/impl/codex/usage.py`): spawn the vendor's
binary, one structured request in, one structured response out. `get_usage` is
documented there as experimental, hence the defensive parsing — every failure
returns None and the caller falls back to the status-line snapshot.

COST. One CLI process per account per CACHE_SECONDS. That is heavy next to a
status-line delivery, which is why the cache is minutes rather than seconds: a
weekly window does not move fast enough to care, and the two account-wide
windows keep arriving on the cheap channel meanwhile.
"""

from __future__ import annotations

import os
import select
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from harness.models import UsageWindowSample
from harness.impl.claude_code.ids import ClaudeCodeControlRequestId

# The CLI's `get_usage` control-response body — a DIFFERENT foreign source
# from the transcript/hook registers (canonical/records.py owns those): a
# live subprocess reply, not a stored document, so a shape mismatch here
# degrades to "no live usage" (usage() falls back to the status-line
# snapshot) rather than a stored `translation_failed` — there is nothing
# recorded to fail. `extra="forbid"` still applies: an unrecognised field is
# exactly as much a sign the CLI's own `get_usage` contract moved as a
# transcript field would be. Every field below is what this module's own
# `windows`/`usage` read; the module's own docstring already calls this
# channel "documented... as experimental, hence the defensive parsing."
_LIVE_FOREIGN = ConfigDict(extra="forbid", frozen=True)


class LiveUsageWindow(BaseModel):
    model_config = _LIVE_FOREIGN
    utilization: float | int | None = None
    resets_at: str | None = None
    limit_dollars: float | int | None = None
    used_dollars: float | int | None = None
    remaining_dollars: float | int | None = None


class LiveModelScopedWindow(BaseModel):
    model_config = _LIVE_FOREIGN
    display_name: str | None = None
    utilization: float | int | None = None
    resets_at: str | None = None


class LiveExtraUsage(BaseModel):
    model_config = _LIVE_FOREIGN
    is_enabled: bool
    monthly_limit: float | int | None
    used_credits: float | int | None
    utilization: float | int | None
    currency: str | None
    decimal_places: int | None
    disabled_reason: str | None
    user_disabled: bool
    spend_limit_reached: bool
    credits_ever_enabled: bool
    daily: LiveUsageWindow | None
    weekly: LiveUsageWindow | None


class LiveLimitModel(BaseModel):
    model_config = _LIVE_FOREIGN
    id: str | None
    display_name: str


class LiveLimitScope(BaseModel):
    model_config = _LIVE_FOREIGN
    model: LiveLimitModel | None
    surface: str | None


class LiveLimit(BaseModel):
    model_config = _LIVE_FOREIGN
    kind: str
    group: str
    percent: float | int
    severity: str
    resets_at: str | None
    scope: LiveLimitScope | None
    is_active: bool


class LiveMoney(BaseModel):
    model_config = _LIVE_FOREIGN
    amount_minor: int
    currency: str
    exponent: int


class LiveSpend(BaseModel):
    model_config = _LIVE_FOREIGN
    used: LiveMoney
    limit: LiveMoney | None
    percent: float | int
    severity: str
    enabled: bool
    disabled_reason: str | None
    cap: LiveMoney | None
    balance: LiveMoney | None
    auto_reload: None
    disclaimer: str
    can_purchase_credits: bool
    can_toggle: bool


class LiveRateLimits(BaseModel):
    model_config = _LIVE_FOREIGN
    five_hour: LiveUsageWindow | None = None
    seven_day: LiveUsageWindow | None = None
    seven_day_oauth_apps: LiveUsageWindow | None = None
    seven_day_opus: LiveUsageWindow | None = None
    seven_day_sonnet: LiveUsageWindow | None = None
    seven_day_cowork: LiveUsageWindow | None = None
    seven_day_omelette: LiveUsageWindow | None = None
    tangelo: LiveUsageWindow | None = None
    iguana_necktie: LiveUsageWindow | None = None
    omelette_promotional: LiveUsageWindow | None = None
    nimbus_quill: LiveUsageWindow | None = None
    cinder_cove: LiveUsageWindow | None = None
    amber_ladder: LiveUsageWindow | None = None
    extra_usage: LiveExtraUsage | None = None
    limits: tuple[LiveLimit, ...] = ()
    spend: LiveSpend | None = None
    member_dashboard_available: bool | None = None
    model_scoped: list[LiveModelScopedWindow] | None = None


class LiveSessionUsage(BaseModel):
    model_config = _LIVE_FOREIGN
    total_cost_usd: float | int
    total_api_duration_ms: float | int
    total_duration_ms: float | int
    total_lines_added: int
    total_lines_removed: int
    # Model identifiers are runtime-defined; the probe session has not invoked
    # a model, so its only valid measured value is an empty dynamic index.
    model_usage: Mapping[str, None]


class LiveBehavior(BaseModel):
    model_config = _LIVE_FOREIGN
    key: str
    pct: float | int
    count: int


class LiveNamedPercentage(BaseModel):
    model_config = _LIVE_FOREIGN
    name: str
    pct: float | int


class LiveBehaviorPeriod(BaseModel):
    model_config = _LIVE_FOREIGN
    request_count: int
    session_count: int
    behaviors: tuple[LiveBehavior, ...]
    agents: tuple[LiveNamedPercentage, ...]
    skills: tuple[LiveNamedPercentage, ...]
    plugins: tuple[LiveNamedPercentage, ...]
    mcp_servers: tuple[LiveNamedPercentage, ...]


class LiveBehaviors(BaseModel):
    model_config = _LIVE_FOREIGN
    day: LiveBehaviorPeriod
    week: LiveBehaviorPeriod


class GetUsageResponse(BaseModel):
    model_config = _LIVE_FOREIGN
    session: LiveSessionUsage
    rate_limits: LiveRateLimits | None = None
    rate_limits_available: bool
    subscription_type: str | None = None
    behaviors: LiveBehaviors | None = None


class NativeOauthAccount(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    organizationType: str | None = None


class NativeUsageCache(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    fetchedAtMs: float | int
    utilization: LiveRateLimits


class NativeProfile(BaseModel):
    """Only the two usage fields read from Claude's large native profile."""

    model_config = ConfigDict(extra="ignore", frozen=True)
    oauthAccount: NativeOauthAccount | None = None
    cachedUsageUtilization: NativeUsageCache | None = None


class GetUsageRequest(BaseModel):
    model_config = _LIVE_FOREIGN
    subtype: Literal["get_usage"] = "get_usage"


class ControlRequestLine(BaseModel):
    model_config = _LIVE_FOREIGN
    type: Literal["control_request"] = "control_request"
    request_id: ClaudeCodeControlRequestId
    request: GetUsageRequest


class ControlResponseBody(BaseModel):
    model_config = _LIVE_FOREIGN
    subtype: Literal["success"]
    request_id: ClaudeCodeControlRequestId
    response: GetUsageResponse | None = None


class ControlResponseLine(BaseModel):
    model_config = _LIVE_FOREIGN
    type: Literal["control_response"]
    response: ControlResponseBody


class ControlResponseIdentityBody(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    request_id: ClaudeCodeControlRequestId | None = None


class ControlResponseIdentity(BaseModel):
    """The two fields needed to identify our response before strict parsing."""

    model_config = ConfigDict(extra="ignore", frozen=True)
    type: str | None = None
    response: ControlResponseIdentityBody | None = None

# The probe must not out-live a refresh cycle by much. Two seconds is the happy
# path; the slow one is the CLI's own retry ladder when the usage endpoint is
# throttled, which it walks before answering with nothing.
PROBE_TIMEOUT_SECONDS = 6.0
CACHE_SECONDS = 600.0
FAILURE_CACHE_SECONDS = 60.0
COMMAND = "claude"
BINARY_DIRECTORIES = (
    "~/.local/bin",
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "~/.hermes/node/bin",
)
REQUEST_ID = ClaudeCodeControlRequestId("baqylau-usage")
REQUEST = ControlRequestLine(request_id=REQUEST_ID, request=GetUsageRequest())

# The environment this process is NOT allowed to inherit into the probe. The
# daemon may itself have been started from inside a session (its shell had these
# exported), and any one of them re-authenticates the probe as somebody else —
# CLAUDE_CODE_OAUTH_TOKEN in particular downgrades it to an inference-only token,
# which is answered with `rate_limits: null` and no Fable window at all.
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
# Set on the probe so our own hook client recognises the session as ours and
# ships nothing: a reader must not manufacture sessions in the store it reads.
# The name is copied in client/_http.py and pinned by tests/test_canonical_clients.py.
PROBE_VARIABLE = "BAQYLAU_USAGE_PROBE"

# A model bucket is a weekly window, so it is keyed like one: `seven_day_fable`
# sits beside `seven_day` in the same vocabulary the status line already writes,
# and every layer below — the two usage tables, the strip's duration columns —
# carries it with no change.
MODEL_WINDOW_PREFIX = "seven_day_"
ACCOUNT_WINDOWS = ("five_hour", "seven_day")
MAX_MODEL_WINDOWS = 6

@dataclass(frozen=True)
class CacheEntry:
    config_directory: str
    expires_at: float
    collection: "LiveUsageCollection"


_cache: list[CacheEntry] = []


@dataclass(frozen=True)
class LiveUsage:
    """One account's windows as the CLI last reported them, and when we asked."""

    captured_at: float
    plan: str | None
    windows: tuple[UsageWindowSample, ...]


@dataclass(frozen=True)
class LiveUsageCollection:
    """The newest usable snapshot and a current collection problem, if any."""

    usage: LiveUsage | None
    error: str | None


@dataclass(frozen=True)
class ProbeResult:
    response: GetUsageResponse | None
    error: str | None


def subprocess_environment(config_directory: str | None) -> Mapping[str, str]:
    environment = os.environ.copy()
    for name in DISCARDED_VARIABLES:
        environment.pop(name, None)
    if config_directory:
        environment[CONFIG_DIRECTORY_VARIABLE] = config_directory
    environment[PROBE_VARIABLE] = "1"
    directories = [
        directory
        for directory in (os.path.expanduser(candidate) for candidate in BINARY_DIRECTORIES)
        if os.path.isdir(directory)
    ]
    if directories:
        environment["PATH"] = os.pathsep.join([*directories, environment.get("PATH", "")])
    return environment


def _epoch_seconds(value: str | None) -> float | None:
    """An ISO 8601 `resets_at` to epoch seconds, or None.

    This channel spells the reset as a timestamp string where the status line
    spells it as a number; both end up in the same column.
    """
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
    """A server-supplied bucket label ("Fable 5") as a window key ("fable_5").

    Server text becomes a stored key here, so it is reduced to the same shape
    every other window key has: lower case, `[a-z0-9_]`, and short.
    """
    if not display_name:
        return None
    slug = "".join(character if character.isalnum() else "_" for character in display_name.lower())
    slug = "_".join(part for part in slug.split("_") if part)
    if not slug or not slug.isascii() or len(slug) > 24:
        return None
    return MODEL_WINDOW_PREFIX + slug


def windows(
    live_rate_limits: LiveRateLimits | None,
) -> tuple[UsageWindowSample, ...]:
    """The account-wide pair first, then one sample per model bucket."""
    if live_rate_limits is None:
        return ()
    samples: dict[str, UsageWindowSample] = {}
    for key, window in (
        ("five_hour", live_rate_limits.five_hour), ("seven_day", live_rate_limits.seven_day)
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
    """The reply to our one request, out of a stream that also carries the
    session's own lifecycle lines."""
    if process.stdout is None:
        return ProbeResult(None, "usage probe has no output stream")
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return ProbeResult(None, "usage probe timed out")
        readable, _, _ = select.select((process.stdout,), (), (), remaining)
        if not readable:
            return ProbeResult(None, "usage probe timed out")
        line = process.stdout.readline()
        if not line:
            return ProbeResult(None, "usage probe ended without a response")
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
            return ProbeResult(
                None,
                f"usage probe schema failure: {error.errors()[0]['type']}",
            )
        document = message.response.response
        if document is None:
            return ProbeResult(None, "usage probe returned no usage document")
        return ProbeResult(document, None)


def request_usage(
    config_directory: str | None,
) -> ProbeResult:
    """One `get_usage` round trip against one account's configuration."""
    try:
        process = subprocess.Popen(
            [COMMAND, "--print", "--verbose",
             "--input-format", "stream-json", "--output-format", "stream-json"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env=subprocess_environment(config_directory),
            # Never the account's own working directory: the probe would be
            # answering for a project it is not part of, and the CLI resolves
            # per-directory settings from wherever it starts.
            cwd=os.path.expanduser("~"),
            start_new_session=True,
        )
    except OSError:
        return ProbeResult(None, "usage probe could not start Claude")
    try:
        if process.stdin is None:
            return ProbeResult(None, "usage probe has no input stream")
        process.stdin.write(REQUEST.model_dump_json() + "\n")
        process.stdin.flush()
        return _control_response(process, time.monotonic() + PROBE_TIMEOUT_SECONDS)
    except OSError:
        return ProbeResult(None, "usage probe communication failed")
    finally:
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        process.terminate()


def native_usage(config_directory: str | None) -> LiveUsageCollection:
    """Read Claude's last profile-scoped usage snapshot for one account."""
    if not config_directory:
        return LiveUsageCollection(None, None)
    profile_path = os.path.join(config_directory, ".claude.json")
    try:
        with open(profile_path, encoding="utf-8") as source:
            profile = NativeProfile.model_validate_json(source.read())
    except OSError:
        return LiveUsageCollection(None, "Claude profile usage cache is unavailable")
    except ValidationError as error:
        return LiveUsageCollection(
            None,
            f"Claude profile usage schema failure: {error.errors()[0]['type']}",
        )
    cached = profile.cachedUsageUtilization
    if cached is None:
        return LiveUsageCollection(None, None)
    samples = windows(cached.utilization)
    if not samples:
        return LiveUsageCollection(None, None)
    organization_type = (
        profile.oauthAccount.organizationType if profile.oauthAccount is not None else None
    )
    return LiveUsageCollection(
        LiveUsage(
            captured_at=float(cached.fetchedAtMs) / 1000,
            plan=organization_type or None,
            windows=samples,
        ),
        None,
    )


def collect(
    config_directory: str | None = None,
) -> LiveUsageCollection:
    """Refresh usage and keep the last good native snapshot on failure."""
    now = time.time()
    cache_key = config_directory or ""
    cached = next((entry for entry in _cache if entry.config_directory == cache_key), None)
    if cached is not None and cached.expires_at > now:
        return cached.collection

    native = native_usage(config_directory)
    if native.usage is not None and now - native.usage.captured_at <= CACHE_SECONDS:
        result = native
    else:
        probed = request_usage(config_directory)
        document = probed.response
        if document is None:
            result = LiveUsageCollection(native.usage, probed.error)
        elif not document.rate_limits_available:
            result = LiveUsageCollection(
                native.usage,
                "Claude profile rate limits are not available for this account",
            )
        else:
            samples = windows(document.rate_limits)
            if samples:
                result = LiveUsageCollection(
                    LiveUsage(
                        captured_at=now,
                        plan=document.subscription_type or None,
                        windows=samples,
                    ),
                    None,
                )
            else:
                result = LiveUsageCollection(
                    native.usage,
                    "Claude profile usage contains no rate-limit windows",
                )
    _cache[:] = [entry for entry in _cache if entry.config_directory != cache_key]
    ttl = CACHE_SECONDS if result.error is None else FAILURE_CACHE_SECONDS
    _cache.append(CacheEntry(cache_key, now + ttl, result))
    return result
