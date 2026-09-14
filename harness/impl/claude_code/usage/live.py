# Copyright (c) 2026 Zhambyl Yermagambet
"""Collect and cache live Claude plan usage."""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

from harness.impl.claude_code import definition
from harness.impl.claude_code.usage import (
    live_documents,
    live_models,
    probe_documents,
    probe_request,
    windows as usage_windows,
)

if TYPE_CHECKING:
    from harness.runtime import HarnessRuntimeConfig

LiveUsageWindow = live_documents.LiveUsageWindow
LiveModelScopedWindow = live_documents.LiveModelScopedWindow
LiveLimitModel = live_documents.LiveLimitModel
LiveLimitScope = live_documents.LiveLimitScope
LiveLimit = live_documents.LiveLimit
LiveRateLimits = live_documents.LiveRateLimits
GetUsageResponse = live_documents.GetUsageResponse
GetUsageRequest = probe_documents.GetUsageRequest
ControlRequestLine = probe_documents.ControlRequestLine
ControlResponseBody = probe_documents.ControlResponseBody
ControlResponseLine = probe_documents.ControlResponseLine
ControlResponseIdentityBody = probe_documents.ControlResponseIdentityBody
ControlResponseIdentity = probe_documents.ControlResponseIdentity
LiveUsage = live_models.LiveUsage
LiveUsageCollection = live_models.LiveUsageCollection
ProbeFailure = live_models.ProbeFailure
ProbeResult = live_models.ProbeResult
CacheEntry = live_models.CacheEntry
subprocess_environment = probe_request.subprocess_environment
request_usage = probe_request.request_usage
windows = usage_windows.windows
PROBE_VARIABLE = probe_request.PROBE_VARIABLE

CACHE_SECONDS = 120.0
RETRY_SECONDS = 5.0
STALE_SECONDS = 300.0
PERMANENT_FAILURE_SECONDS = 60.0

_cache: list[live_models.CacheEntry] = []
_cache_lock = threading.Lock()


def _default_runtime_config() -> HarnessRuntimeConfig:
    return definition.default_runtime_config()


def collect(
    harness_runtime_config: HarnessRuntimeConfig | None = None,
) -> live_models.LiveUsageCollection:
    """Return fresh usage or a valid cached value.

    Returns:
        The current live usage collection.

    """
    runtime_config = harness_runtime_config or _default_runtime_config()
    with _cache_lock:
        return _collect(runtime_config)


def _collect(
    harness_runtime_config: HarnessRuntimeConfig,
) -> live_models.LiveUsageCollection:
    now = time.time()
    cache_key = f"{harness_runtime_config.executable}\0{harness_runtime_config.configuration_directory}"
    cached = next((entry for entry in _cache if entry.runtime_key == cache_key), None)
    if cached is not None and cached.expires_at > now:
        return cached.collection
    resolution = _resolve_live_probe(
        request_usage(harness_runtime_config),
        cached,
        now,
    )
    _store_cache_entry(
        live_models.CacheEntry(
            cache_key,
            now + resolution.cache_seconds,
            resolution.collection,
            resolution.last_good,
        ),
    )
    return resolution.collection


def _resolve_live_probe(
    probe: live_models.ProbeResult,
    cached: live_models.CacheEntry | None,
    now: float,
) -> live_models.LiveResolution:
    last_good = None if cached is None else cached.last_good
    document = probe.response
    if document is not None:
        return _live_document_resolution(document, now)
    if probe.failure is not None and probe.failure.authentication_error:
        return live_models.LiveResolution(
            live_models.LiveUsageCollection(None, None, probe.failure.message), None, RETRY_SECONDS,
        )
    if probe.failure is not None and probe.failure.recoverable:
        if last_good is not None and now - last_good.captured_at > STALE_SECONDS:
            last_good = None
        return live_models.LiveResolution(
            live_models.LiveUsageCollection(last_good, None),
            last_good,
            RETRY_SECONDS,
        )
    message = "Claude usage failed" if probe.failure is None else probe.failure.message
    return live_models.LiveResolution(
        live_models.LiveUsageCollection(None, message),
        None,
        PERMANENT_FAILURE_SECONDS,
    )


def _live_document_resolution(
    document: live_documents.GetUsageResponse,
    now: float,
) -> live_models.LiveResolution:
    if not document.rate_limits_available:
        return live_models.LiveResolution(
            live_models.LiveUsageCollection(
                None,
                "Claude plan usage is not available for this account",
            ),
            None,
            PERMANENT_FAILURE_SECONDS,
        )
    samples = windows(document.rate_limits)
    if not samples:
        return live_models.LiveResolution(
            live_models.LiveUsageCollection(
                None,
                "Claude usage response contains no limit windows",
            ),
            None,
            PERMANENT_FAILURE_SECONDS,
        )
    usage = live_models.LiveUsage(
        captured_at=now,
        plan=document.subscription_type or None,
        windows=samples,
    )
    return live_models.LiveResolution(
        live_models.LiveUsageCollection(usage, None),
        usage,
        CACHE_SECONDS,
    )


def _store_cache_entry(cache_entry: live_models.CacheEntry) -> None:
    for previous_entry in tuple(_cache):
        if previous_entry.runtime_key == cache_entry.runtime_key:
            _cache.remove(previous_entry)
    _cache.append(cache_entry)
