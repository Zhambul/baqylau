# Copyright (c) 2026 Zhambyl Yermagambet
"""Define results and cache records for live Claude usage."""

from __future__ import annotations

from dataclasses import dataclass

from harness.impl.claude_code.usage.live_documents import GetUsageResponse
from harness.models.usage import UsageWindowSample


@dataclass(frozen=True)
class LiveUsage:
    """Hold one successful live usage sample."""

    captured_at: float
    plan: str | None
    windows: tuple[UsageWindowSample, ...]


@dataclass(frozen=True)
class LiveUsageCollection:
    """Hold the last valid usage and one permanent error."""

    usage: LiveUsage | None
    error: str | None
    authentication_error: str | None = None


@dataclass(frozen=True)
class ProbeFailure:
    """Describe one usage-probe failure."""

    message: str
    recoverable: bool
    authentication_error: bool = False


@dataclass(frozen=True)
class ProbeResult:
    """Hold either a usage response or a probe failure."""

    response: GetUsageResponse | None
    failure: ProbeFailure | None


@dataclass(frozen=True)
class CacheEntry:
    """Hold cached usage for one runtime configuration."""

    runtime_key: str
    expires_at: float
    collection: LiveUsageCollection
    last_good: LiveUsage | None


@dataclass(frozen=True)
class LiveResolution:
    """Describe the cache update after one probe."""

    collection: LiveUsageCollection
    last_good: LiveUsage | None
    cache_seconds: float
