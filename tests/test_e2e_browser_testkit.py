"""Deterministic checks for browser E2E usage selection."""

from decimal import Decimal

import pytest

from api.common.models.values.usage_row import UsageRowResponse, UsageWindowResponse
from harness.models import UsageWindowScope
from tests.e2e.testkit.browser import default_model_usage_window


def _row(
    *,
    collection_error: str | None = None,
    account_id: str | None = None,
    switchable: bool = False,
) -> UsageRowResponse:
    return UsageRowResponse(
        harness="claude_code",
        account_id=account_id,
        display_name="claude",
        switchable=switchable,
        default_for_launch=True,
        plan="team",
        windows=(
            UsageWindowResponse(
                key="seven_day_fable",
                label="7d fable",
                used_percent=Decimal("21"),
                resets_at=2_000_000_000,
                duration_minutes=10_080,
                scope=UsageWindowScope.MODEL,
                model_id="fable",
            ),
        ),
        scheduling_score=None,
        scheduling_allowed=False,
        limit=None,
        authentication_error=None,
        collection_error=collection_error,
    )


def test_usage_selection_fails_when_refresh_failed():
    with pytest.raises(
        AssertionError,
        match="usage refresh failed: claude_code: profile refresh failed",
    ):
        default_model_usage_window(
            (_row(collection_error="profile refresh failed"),),
            "claude_code",
            "fable",
        )


def test_usage_selection_waits_for_initial_publication():
    assert default_model_usage_window((), "claude_code", "fable") is None


def test_usage_selection_retries_a_transient_probe_timeout():
    assert default_model_usage_window(
        (_row(collection_error="usage probe timed out"),),
        "claude_code",
        "fable",
    ) is None


def test_usage_selection_rejects_a_claude_account_selector():
    with pytest.raises(AssertionError, match="published an account selection"):
        default_model_usage_window(
            (_row(account_id="legacy-account", switchable=True),),
            "claude_code",
            "fable",
        )
