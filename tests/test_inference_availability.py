# Copyright (c) 2026 Zhambyl Yermagambet
"""Inference provider availability and timeout tests."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from harness.models.usage import UsageRow, UsageWindow, UsageWindowScope
from inference import (
    contract as inference_contract,
    errors as inference_errors,
)
from tests.harness_names import CLAUDE_CODE_HARNESS, CODEX_HARNESS
from tests.inference_support import Audit, InferenceTerminal, Usage, factory

if TYPE_CHECKING:
    from domain.ids import HarnessName

TEST_SESSION_ID = "session-one"
MODEL_PROMPT = "name this"


def usage_row(harness: HarnessName, used_percent: Decimal) -> UsageRow:
    """Return one usage row.

    Returns:
        One usage row.

    """
    return UsageRow(
        harness=harness,
        account_id=None,
        display_name=str(harness),
        switchable=False,
        default_for_launch=False,
        plan=None,
        windows=(
            UsageWindow(
                key="limit",
                label="limit",
                used_percent=used_percent,
                resets_at=None,
                duration_minutes=None,
                scope=UsageWindowScope.ACCOUNT,
                model_name=None,
            ),
        ),
        scheduling_score=None,
        scheduling_allowed=False,
        limit=None,
        authentication_error=None,
    )


def test_timeout_closes_every_attempted_provider() -> None:
    """Verify timeout closes each provider window."""
    audit = Audit()
    terminal = InferenceTerminal(("", "", "", ""), stays_open=True)
    with pytest.raises(inference_errors.ModelUnavailableError):
        factory(terminal, audit=audit, timeout=-1).small().send(
            inference_contract.ModelPromptRequest(MODEL_PROMPT, TEST_SESSION_ID),
        )
    assert terminal.closed_tabs == ["model-1", "model-2", "model-3", "model-4"]
    assert len(audit.errors) == 1


def test_exhausted_known_quotas_do_not_open_any() -> None:
    """Verify exhausted quotas do not open a provider."""
    audit = Audit()
    terminal = InferenceTerminal(())
    exhausted = Usage(
        (
            usage_row(CODEX_HARNESS, Decimal(100)),
            usage_row(CLAUDE_CODE_HARNESS, Decimal(100)),
        ),
    )
    with pytest.raises(inference_errors.ModelUnavailableError):
        factory(terminal, exhausted, audit).small().send(
            inference_contract.ModelPromptRequest(MODEL_PROMPT, TEST_SESSION_ID),
        )
    assert not terminal.opened_tabs
    assert len(audit.errors) == 1
