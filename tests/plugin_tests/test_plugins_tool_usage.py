# Copyright (c) 2026 Zhambyl Yermagambet
"""Codex usage probe tests."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from harness.impl.codex import (
    usage as codex_usage,
    usage_rate_limit_documents as codex_usage_documents,
    usage_rpc as codex_usage_rpc,
)
from harness.impl.codex.usage_rows import CodexUsage
from harness.runtime import (
    HarnessRuntimeConfig,
)
from tests.harness_names import CODEX_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_values import JsonValue

if TYPE_CHECKING:
    import pytest

type CodexTranslationDecisionCase = tuple[dict[str, JsonValue], str]


def test_codex_current_app_server_rate_limits() -> None:
    """Verify codex current app server rate limits are strictly typed and normalized."""
    response = codex_usage_rpc.RateLimitsRpcResponse.model_validate(
        {
            fixture.ID_FIELD: 2,
            fixture.RESULT: {
                "rateLimits": {
                    "limitId": fixture.CODEX_HARNESS,
                    "limitName": None,
                    "primary": {
                        "usedPercent": fixture.RATE_LIMIT_USED_PERCENT,
                        "windowDurationMins": fixture.WEEKLY_RATE_LIMIT_MINUTES,
                        "resetsAt": 1787879978,
                    },
                    "secondary": None,
                    "credits": {"hasCredits": False, "unlimited": False, "balance": fixture.ZERO_TEXT},
                    "individualLimit": None,
                    "spendControlReached": False,
                    "planType": "prolite",
                    "rateLimitReachedType": None,
                },
                "rateLimitsByLimitId": {
                    fixture.CODEX_HARNESS: {
                        "limitId": fixture.CODEX_HARNESS,
                        "limitName": None,
                        "primary": {
                            "usedPercent": 12,
                            "windowDurationMins": 10080,
                            "resetsAt": 1787879978,
                        },
                        "secondary": None,
                        "credits": {"hasCredits": False, "unlimited": False, "balance": fixture.ZERO_TEXT},
                        "individualLimit": None,
                        "spendControlReached": False,
                        "planType": "prolite",
                        "rateLimitReachedType": None,
                    },
                },
                "rateLimitResetCredits": {
                    "availableCount": 1,
                    "credits": [
                        {
                            fixture.ID_FIELD: "credit-one",
                            "resetType": "codexRateLimits",
                            fixture.STATUS_FIELD: "available",
                            "grantedAt": 1787358029,
                            "expiresAt": 1789950029,
                            fixture.TITLE_FIELD: "Full reset",
                            fixture.DESCRIPTION_FIELD: "One free rate limit reset.",
                        },
                    ],
                },
            },
        },
    )

    normalized = codex_usage.normalize_rate_limits(response.result)
    assert normalized is not None
    assert normalized.plan == "prolite"
    assert normalized.windows[0].used_percent == fixture.RATE_LIMIT_USED_PERCENT
    assert normalized.windows[0].duration_minutes == fixture.WEEKLY_RATE_LIMIT_MINUTES


def test_codex_usage_retries_transient_app_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify codex usage retries a transient app server miss."""
    response = codex_usage_documents.AccountRateLimitsResponse(
        rate_limits=codex_usage_documents.RateLimitsResult(
            primary=codex_usage_documents.RateLimitWindowResult(
                used_percent=fixture.RATE_LIMIT_USED_PERCENT,
                window_duration_mins=fixture.SHORT_RATE_LIMIT_MINUTES,
                resets_at=fixture.RATE_LIMIT_RESET_TIME,
            ),
            plan_type="available",
        ),
    )
    responses = iter(
        (
            codex_usage.ProbeResult(
                None,
                codex_usage.ProbeFailure(message="Codex usage request timed out", recoverable=True),
            ),
            codex_usage.ProbeResult(response, None),
        ),
    )
    now = iter((100.0, 103.0))
    monkeypatch.setattr(codex_usage.rate_limit_cache_store, "cache_entry", None)
    monkeypatch.setattr(
        codex_usage,
        "request_rate_limits",
        lambda _runtime: next(responses),
    )
    monkeypatch.setattr("harness.impl.codex.usage.time.time", lambda: next(now))

    assert codex_usage.collect_rate_limits().usage is None
    refreshed = codex_usage.collect_rate_limits().usage
    assert refreshed is not None
    assert refreshed.plan == "available"
    assert refreshed.windows[0].used_percent == fixture.RATE_LIMIT_USED_PERCENT


def test_codex_temporary_failure_keeps_last(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify codex temporary failure keeps the last provider result."""
    expected = codex_usage.NormalizedRateLimits(plan="pro", windows=())
    monkeypatch.setattr(
        codex_usage.rate_limit_cache_store,
        "cache_entry",
        codex_usage.CacheEntry(
            "codex\0/work/codex-home",
            fixture.RATE_LIMIT_CACHE_TIME,
            codex_usage.RateLimitsCollection(expected, None),
            expected,
            fixture.RATE_LIMIT_CACHE_EXPIRY_TIME,
        ),
    )
    monkeypatch.setattr("harness.impl.codex.usage.time.time", lambda: 100.0)
    monkeypatch.setattr(
        codex_usage,
        "request_rate_limits",
        lambda _runtime: codex_usage.ProbeResult(
            None,
            codex_usage.ProbeFailure(message="Codex app server ended early", recoverable=True),
        ),
    )

    collection = codex_usage.collect_rate_limits(
        HarnessRuntimeConfig(fixture.CODEX_HARNESS, Path(fixture.WORK_CODEX_HOME_PATH)),
    )

    assert collection.usage == expected
    assert collection.error is None


def test_codex_temporary_failure_does_not_keep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify codex temporary failure does not keep an old result."""
    expected = codex_usage.NormalizedRateLimits(plan="pro", windows=())
    monkeypatch.setattr(
        codex_usage.rate_limit_cache_store,
        "cache_entry",
        codex_usage.CacheEntry(
            "codex\0/work/codex-home",
            fixture.RATE_LIMIT_CACHE_TIME,
            codex_usage.RateLimitsCollection(expected, None),
            expected,
            0,
        ),
    )
    monkeypatch.setattr(
        "harness.impl.codex.usage.time.time",
        lambda: fixture.EXPIRED_USAGE_CLOCK_TIME,
    )
    monkeypatch.setattr(
        codex_usage,
        "request_rate_limits",
        lambda _runtime: codex_usage.ProbeResult(
            None,
            codex_usage.ProbeFailure(message="Codex app server ended early", recoverable=True),
        ),
    )

    collection = codex_usage.collect_rate_limits(
        HarnessRuntimeConfig(fixture.CODEX_HARNESS, Path(fixture.WORK_CODEX_HOME_PATH)),
    )

    assert collection == codex_usage.RateLimitsCollection(None, None)


def test_codex_usage_keeps_visible_row_when(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify codex usage keeps a visible row when the native probe fails."""
    monkeypatch.setattr(
        codex_usage,
        "collect_rate_limits",
        lambda _runtime: codex_usage.RateLimitsCollection(
            None,
            "Codex login was revoked",
        ),
    )
    rows = CodexUsage(
        HarnessRuntimeConfig(fixture.CODEX_HARNESS, Path(fixture.WORK_CODEX_HOME_PATH)),
    ).read()

    assert len(rows) == 1
    assert rows[0].harness == CODEX_HARNESS
    assert rows[0].windows == ()
    assert rows[0].collection_error == "Codex login was revoked"
