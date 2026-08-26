from dataclasses import replace
from decimal import Decimal

from domain.ids import HarnessName
from harness.models import UsageRow
from harness.services.usage import (
    USAGE_INITIAL_DELAY_VARIABLE,
    USAGE_REFRESH_SECONDS,
    USAGE_REFRESH_VARIABLE,
    USAGE_SHARED_CACHE_SECONDS_VARIABLE,
    ApplicationUsageState,
    SharedUsageCache,
    UsageCacheDocument,
    USAGE_CACHE_DOCUMENT,
)


class UsageSource:
    def __init__(self, rows=()):
        self.rows = rows
        self.calls = 0

    def read(self):
        self.calls += 1
        return self.rows


class RetryStop:
    def __init__(self, source):
        self.source = source
        self.delays = []
        self.stopped = False

    def is_set(self):
        return self.stopped

    def wait(self, delay):
        self.delays.append(delay)
        self.stopped = self.source.calls >= 2
        return self.stopped


def test_application_usage_state_uses_configured_polling_intervals(monkeypatch):
    monkeypatch.setenv(USAGE_INITIAL_DELAY_VARIABLE, "12.5")
    monkeypatch.setenv(USAGE_REFRESH_VARIABLE, "60")

    state = ApplicationUsageState.configured(UsageSource())

    assert state.initial_delay_seconds == 12.5
    assert state.refresh_seconds == 60.0


def test_application_usage_state_falls_back_for_invalid_polling_intervals(monkeypatch):
    monkeypatch.setenv(USAGE_INITIAL_DELAY_VARIABLE, "invalid")
    monkeypatch.setenv(USAGE_REFRESH_VARIABLE, "invalid")

    state = ApplicationUsageState.configured(UsageSource())

    assert state.initial_delay_seconds == 0.0
    assert state.refresh_seconds == USAGE_REFRESH_SECONDS


def test_harness_usage_service_uses_configured_shared_cache_age(monkeypatch, tmp_path):
    from harness.services.usage import HarnessUsageService

    monkeypatch.setenv("BAQYLAU_USAGE_SHARED_CACHE", str(tmp_path / "usage.json"))
    monkeypatch.setenv(USAGE_SHARED_CACHE_SECONDS_VARIABLE, "600")

    service = HarnessUsageService(object(), object())

    assert service.shared_cache is not None
    assert service.shared_cache.max_age_seconds == 600


def test_application_usage_state_retries_after_a_transient_source_failure():
    class FlakyUsageSource(UsageSource):
        def read(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("transient probe failure")
            return self.rows

    source = FlakyUsageSource()
    stop = RetryStop(source)

    ApplicationUsageState(source, refresh_seconds=60).run(stop)

    assert source.calls == 2
    assert stop.delays == [5.0, 60]


def test_application_usage_state_publishes_only_changed_rows():
    row = UsageRow(
        HarnessName.CODEX,
        None,
        "Default",
        False,
        True,
        "pro",
        (),
        Decimal("1"),
        True,
        None,
        None,
    )
    source = UsageSource()
    changes = []
    state = ApplicationUsageState(source, changed=lambda: changes.append("changed"))

    state.refresh()
    source.rows = (row,)
    state.refresh()
    state.refresh()

    assert changes == ["changed"]


def test_shared_usage_cache_runs_one_probe_for_multiple_readers(tmp_path):
    row = UsageRow(
        HarnessName.CODEX,
        None,
        "Default",
        False,
        True,
        "pro",
        (),
        Decimal("1"),
        True,
        None,
        None,
    )
    source = UsageSource((row,))
    first = SharedUsageCache(tmp_path / "usage.json")
    second = SharedUsageCache(tmp_path / "usage.json")

    assert first.read(source) == (row,)
    assert second.read(source) == (row,)
    assert source.calls == 1


def test_shared_usage_cache_retries_a_failed_snapshot_quickly(tmp_path, monkeypatch):
    cache_path = tmp_path / "usage.json"
    failed = UsageRow(
        HarnessName.CLAUDE_CODE,
        None,
        "Default",
        False,
        True,
        None,
        (),
        None,
        True,
        None,
        None,
        "temporary failure",
    )
    cache_path.write_bytes(
        USAGE_CACHE_DOCUMENT.dump_json(UsageCacheDocument(10.0, (failed,)))
    )
    source = UsageSource((replace(failed, collection_error=None),))
    monkeypatch.setattr("harness.services.usage.time.time", lambda: 16.0)

    rows = SharedUsageCache(cache_path).read(source)

    assert rows[0].collection_error is None
    assert source.calls == 1
