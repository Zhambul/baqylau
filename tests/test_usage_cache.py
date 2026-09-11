# Copyright (c) 2026 Zhambyl Yermagambet
"""Verify the shared usage cache."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING

from harness.models.usage import UsageRow
from harness.services.usage import (
    USAGE_CACHE_DOCUMENT,
    SharedUsageCache,
    UsageCacheDocument,
)
from tests.harness_names import CLAUDE_CODE_HARNESS, CODEX_HARNESS
from tests.usage_test_support import RecordingUsageSource

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

FAILED_CACHE_TIME = 10.0
RETRY_READ_TIME = 16.0
EXPECTED_SOURCE_READS = 1


def test_shared_usage_cache_runs_one_probe(tmp_path: Path) -> None:
    """Verify the shared cache runs one probe for multiple readers."""
    usage_row = UsageRow(
        harness=CODEX_HARNESS,
        account_id=None,
        display_name="Default",
        switchable=False,
        default_for_launch=True,
        plan="pro",
        windows=(),
        scheduling_score=Decimal(1),
        scheduling_allowed=True,
        limit=None,
        authentication_error=None,
    )
    source = RecordingUsageSource((usage_row,))
    first_cache = SharedUsageCache(tmp_path / "usage.json")
    second_cache = SharedUsageCache(tmp_path / "usage.json")

    assert first_cache.read(source) == (usage_row,)
    assert second_cache.read(source) == (usage_row,)
    assert source.calls == EXPECTED_SOURCE_READS


def test_shared_usage_cache_retries_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify the shared cache retries a failed snapshot quickly."""
    cache_path = tmp_path / "usage.json"
    failed_row = UsageRow(
        harness=CLAUDE_CODE_HARNESS,
        account_id=None,
        display_name="Default",
        switchable=False,
        default_for_launch=True,
        plan=None,
        windows=(),
        scheduling_score=None,
        scheduling_allowed=True,
        limit=None,
        authentication_error=None,
        collection_error="temporary failure",
    )
    cache_path.write_bytes(
        USAGE_CACHE_DOCUMENT.dump_json(UsageCacheDocument(FAILED_CACHE_TIME, (failed_row,))),
    )
    source = RecordingUsageSource((replace(failed_row, collection_error=None),))
    monkeypatch.setattr("harness.services.usage.time.time", lambda: RETRY_READ_TIME)

    usage_rows = SharedUsageCache(cache_path).read(source)

    assert usage_rows[0].collection_error is None
    assert source.calls == EXPECTED_SOURCE_READS
