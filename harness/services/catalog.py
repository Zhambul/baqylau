"""The menu vocabulary one harness offers where a session is."""

from __future__ import annotations

from domain.errors import UnsupportedRequest
from harness.models import HarnessCatalogSnapshot, QueryContext
from harness.registry import HarnessRegistry


class HarnessCatalogService:
    def __init__(self, registry: HarnessRegistry) -> None:
        self.registry = registry

    def read(self, harness: str, context: QueryContext) -> HarnessCatalogSnapshot:
        catalog = self.registry.plugin(harness).catalog
        if catalog is None:
            # Installed, but it offers no menu — the request is the caller's to
            # fix, so it is typed rather than a bare ValueError.
            raise UnsupportedRequest(f"harness {harness!r} has no catalog")
        return catalog.read(context)
