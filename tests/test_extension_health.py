# Copyright (c) 2026 Zhambyl Yermagambet
"""Count consecutive extension failures durably and report the limit once (P03-T05)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from extensions.models.extension_health import ExtensionHealth, HealthFailure, HealthPolicy, HealthState
from extensions.pass_health import HealthTracker
from repository.impl.sqlite.extension_health import SqliteExtensionHealth

if TYPE_CHECKING:
    from repository.impl.sqlite.connection import SqliteDatabase

OWNER = "test.failing"
OTHER = "test.other"
LIMIT = 3
FAILED_AT = 1.0
SUCCEEDED_AT = 2.0


def failure(owner: str = OWNER, limit: int = LIMIT) -> HealthFailure:
    """Name one projection failure of the fixture owner.

    Returns:
        The failure at a fixed time.

    """
    return HealthFailure(extension_id=owner, where="extension projection", at=FAILED_AT, limit=limit)


def test_store_counts_and_success_resets(main: SqliteDatabase) -> None:
    """Failures count up to failed; a success makes the extension healthy with no count."""
    store = SqliteExtensionHealth(main)

    states = [store.record_failure(failure()).state for _ in range(LIMIT)]
    store.record_success(OWNER, SUCCEEDED_AT)

    assert states == [HealthState.FAILING, HealthState.FAILING, HealthState.FAILED]
    assert store.read_health() == (ExtensionHealth(
        extension_id=OWNER, state=HealthState.HEALTHY, consecutive_failures=0,
        last_failure_where="extension projection", last_failure_at=FAILED_AT, last_success_at=SUCCEEDED_AT,
    ),)


def test_limit_of_one_fails_at_once(main: SqliteDatabase) -> None:
    """The first failure is failed when the limit is one."""
    assert SqliteExtensionHealth(main).record_failure(failure(limit=1)).state == HealthState.FAILED


@dataclass
class CountingStore:
    """Count the success writes of the real store."""

    store: SqliteExtensionHealth
    successes: list[str] = field(default_factory=list)

    def read_health(self) -> tuple[ExtensionHealth, ...]:
        """Read through the real store.

        Returns:
            The stored rows.

        """
        return self.store.read_health()

    def record_failure(self, health_failure: HealthFailure) -> ExtensionHealth:
        """Write through the real store.

        Returns:
            The new health.

        """
        return self.store.record_failure(health_failure)

    def record_success(self, owner: str, at: float) -> None:
        """Keep the owner of each success write, then write it."""
        self.successes.append(owner)
        self.store.record_success(owner, at)


def test_tracker_reports_limit_once(main: SqliteDatabase) -> None:
    """The failed callback runs once at the limit; a healthy owner's success writes nothing."""
    store = CountingStore(SqliteExtensionHealth(main))
    reached: list[ExtensionHealth] = []
    policy = HealthPolicy(LIMIT)
    tracker = HealthTracker(store, clock=lambda: FAILED_AT, on_failed=reached.append, policy=policy)
    stage = tracker.stage("extension projection", audit=lambda _owner: None)

    stage.succeeded(OTHER)
    for _ in range(LIMIT + 1):
        stage.failed(OWNER)
    stage.succeeded(OWNER)
    stage.succeeded(OWNER)

    assert [health.extension_id for health in reached] == [OWNER]
    assert store.successes == [OWNER]


def test_tracker_reads_failures_after_restart(main: SqliteDatabase) -> None:
    """A new tracker clears the failures that an earlier daemon stored."""
    SqliteExtensionHealth(main).record_failure(failure())
    store = CountingStore(SqliteExtensionHealth(main))
    tracker = HealthTracker(store, clock=lambda: SUCCEEDED_AT, on_failed=lambda _health: None)

    tracker.succeeded(OWNER)

    assert store.successes == [OWNER]
    assert store.read_health()[0].state == HealthState.HEALTHY
