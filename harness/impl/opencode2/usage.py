# Copyright (c) 2026 Zhambyl Yermagambet
"""Read OpenCode Go limits through the native credential owner."""

import subprocess  # noqa: S404 -- Handle native API client failures.
import time
from http import HTTPStatus

from pydantic import ValidationError

from harness.contract import HarnessUsage
from harness.impl.opencode2 import usage_probe, usage_windows
from harness.impl.opencode2.sources import HARNESS
from harness.models.usage import UsageRow, UsageWindow
from harness.runtime import HarnessRuntimeConfig


class OpenCodeUsage(HarnessUsage):
    """Cache account limits between native API requests."""

    def __init__(self, harness_runtime_config: HarnessRuntimeConfig) -> None:
        """Use the installed executable and the configured plugin directory."""
        self.runtime = harness_runtime_config
        self.next_read: float = 0
        self.rows: tuple[UsageRow, ...] = ()

    def read(self) -> tuple[UsageRow, ...]:
        """Return account limits or an explicit collection error.

        Returns:
            One account row.

        """
        if time.monotonic() < self.next_read:
            return self.rows
        row = self._collect()
        self.rows = (row,)
        cooldown = 60
        if row.collection_error or row.authentication_error:
            cooldown = 5
        self.next_read = time.monotonic() + cooldown
        return self.rows

    def _collect(self) -> UsageRow:
        error = None
        authentication_error = None
        windows: tuple[UsageWindow, ...] = ()
        try:
            result = usage_probe.request(self.runtime)
        except (OSError, subprocess.SubprocessError, ValidationError):
            error = "OpenCode Go limit collection failed"
        else:
            if result.status == HTTPStatus.OK and result.usage is not None:
                windows = usage_windows.windows(result.usage)
            elif result.status == HTTPStatus.UNAUTHORIZED:
                authentication_error = "Sign in to OpenCode Go"
            else:
                error = f"OpenCode Go limits are unavailable (HTTP {result.status})"
        return UsageRow(
            harness=HARNESS, account_id=None, display_name="OpenCode2",
            switchable=False, default_for_launch=False, plan="Go" if windows else None,
            windows=windows, scheduling_score=None, scheduling_allowed=False,
            limit=None, authentication_error=authentication_error, collection_error=error,
        )
