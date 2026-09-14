# Copyright (c) 2026 Zhambyl Yermagambet
"""Supply the native commands supported by the dashboard."""

import threading
import time

from harness.contract import HarnessCatalog
from harness.impl.opencode2 import definition, model_catalog
from harness.models.catalog import CommandOption, HarnessCatalogSnapshot, QueryContext
from harness.runtime import HarnessRuntimeConfig


class OpenCodeCatalog(HarnessCatalog):
    """Read native models and the OpenCode2 command menu."""

    def __init__(self, harness_runtime_config: HarnessRuntimeConfig | None = None) -> None:
        """Keep native discovery outside module import."""
        self.runtime = harness_runtime_config or definition.default_runtime_config()
        self.snapshot = HarnessCatalogSnapshot()
        self.next_read: float = 0
        self.lock = threading.Lock()

    def read(self, query_context: QueryContext) -> HarnessCatalogSnapshot:  # noqa: ARG002 -- Native controls do not depend on the directory.
        """Return commands that do not depend on the working directory.

        Returns:
            The command menu.

        """
        with self.lock:
            if time.monotonic() >= self.next_read:
                self.snapshot = HarnessCatalogSnapshot(
                    commands=(
                        CommandOption("compact", "Compact the session context", 1),
                        CommandOption("variants", "Select the model effort", 0),
                    ),
                    models=model_catalog.read(self.runtime),
                )
                self.next_read = time.monotonic() + 60
            return self.snapshot
