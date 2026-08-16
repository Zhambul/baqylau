"""The menu vocabulary one harness offers where a session is."""

from __future__ import annotations

from harness.models import HarnessCatalogSnapshot, QueryContext
from harness.registry import HarnessRegistry


class HarnessCatalogService:
    def __init__(self, registry: HarnessRegistry) -> None:
        self.registry = registry

    def read(self, harness: str, context: QueryContext) -> HarnessCatalogSnapshot:
        catalog = self.registry.plugin(harness).catalog
        if catalog is None:
            raise ValueError(f"harness {harness!r} has no catalog")
        return catalog.read(context)
