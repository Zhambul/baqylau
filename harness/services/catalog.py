"""The menu vocabulary one harness offers where a session is."""

from __future__ import annotations

from domain.errors import UnsupportedRequest
from harness.models import HarnessCatalogSnapshot, QueryContext
from harness.registry import HarnessRegistry


class HarnessCatalogService:
    def __init__(self, harness_registry: HarnessRegistry) -> None:
        self.registry = harness_registry

    def read(self, harness: str, query_context: QueryContext) -> HarnessCatalogSnapshot:
        catalog = self.registry.plugin(harness).catalog
        if catalog is None:
            # Installed, but it offers no menu — the request is the caller's to
            # fix, so it is typed rather than a bare ValueError.
            raise UnsupportedRequest(f"harness {harness!r} has no catalog")
        return catalog.read(query_context)
