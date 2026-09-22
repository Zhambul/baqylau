# Copyright (c) 2026 Zhambyl Yermagambet
"""Check exact shutdown history and fail only private test storage writes."""

from collections.abc import Iterator
from contextlib import contextmanager

from extensions.models.cleanup import RetirementIssue, ShutdownRecord
from repository.impl.sqlite.extension_lifecycle import SqliteExtensionLifecycleRepository
from tests.extension_api import samples, service_samples


def records(store: SqliteExtensionLifecycleRepository) -> tuple[ShutdownRecord, ...]:
    """Read full private test history, including records before the latest observation.

    Returns:
        Exact stored observation in acceptance order.

    """
    with store.database.read() as connection:
        rows = connection.execute("SELECT observation FROM extension_shutdown_records ORDER BY cursor").fetchall()
    return tuple(ShutdownRecord.model_validate_json(str(row["observation"])) for row in rows)


def stop_issue(*, failed_stop: bool) -> RetirementIssue:
    """Select expected uncertainty independently of the host close result.

    Returns:
        A lost reply or the exact unresolved job from the fixture.

    """
    return RetirementIssue(
        runtime_revision=samples.RUNTIME_REVISION, extension_id=service_samples.ALPHA,
        reason="deactivation_failed" if failed_stop else "unresolved_jobs",
        pending_job_ids=() if failed_stop else ("uncertain-job",),
    )


@contextmanager
def reject_records(store: SqliteExtensionLifecycleRepository, after: int) -> Iterator[None]:
    """Fail one shutdown stage and remove the test fault before retrying close.

    Yields:
        No writable connection; only the test's private trigger remains active.

    """
    with store.database.write() as connection:
        connection.execute("CREATE TABLE shutdown_fault(stop_after INTEGER)")
        connection.execute("INSERT INTO shutdown_fault VALUES(?)", (after,))
        connection.execute(
            "CREATE TRIGGER reject_shutdown BEFORE INSERT ON extension_shutdown_records "
            "WHEN (SELECT COUNT(*) FROM extension_shutdown_records)=(SELECT stop_after FROM shutdown_fault) "
            "BEGIN SELECT RAISE(ABORT, 'injected shutdown storage failure'); END",
        )
    try:
        yield
    finally:
        with store.database.write() as connection:
            connection.execute("DROP TRIGGER reject_shutdown")
            connection.execute("DROP TABLE shutdown_fault")
