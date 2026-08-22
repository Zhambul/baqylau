"""Reading harness plan usage from the web dashboard application document."""

from pydantic import TypeAdapter
from pytest_bdd import parsers, then

from api.application.models.preferences.global_application_response import (
    GlobalApplicationResponse,
)
from support import observe
from support.daemon import Daemon

APPLICATION = TypeAdapter(GlobalApplicationResponse)


@then(parsers.parse(
    "the dashboard reports {harness} usage with at least {count:d} window within {seconds:d} seconds"
))
def _dashboard_reports_usage(
    daemon: Daemon, harness: str, count: int, seconds: int,
) -> None:
    """Wait for the real daemon's background usage probe, then read the same
    response document that paints the web dashboard's usage strip."""

    def rows_with_windows():
        application = daemon.read("/api/application", APPLICATION)
        rows = tuple(
            row
            for row in application.usage_rows
            if row.harness == harness and len(row.windows) >= count
        )
        return rows or None

    rows = observe.until(
        f"{harness} usage with at least {count} window(s) to reach the web dashboard",
        rows_with_windows,
        timeout=float(seconds),
    )
    assert all(window.duration_minutes for row in rows for window in row.windows)
