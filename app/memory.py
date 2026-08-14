"""Route the optional typed memory capability by canonical session ownership."""

from __future__ import annotations

from dataclasses import dataclass

from contracts.harness import HarnessMemory, HarnessMemorySnapshot, MemoryDocument
from domain.ids import SessionId
from runtime.projections import SessionQueries
from runtime.registry import HarnessRegistry


@dataclass(frozen=True)
class MemoryStatus:
    enabled: bool
    item_count: int


class MemoryService:
    def __init__(self, registry: HarnessRegistry, queries: SessionQueries) -> None:
        self.registry = registry
        self.queries = queries

    def status(self, session_id: SessionId) -> MemoryStatus:
        summary = self.queries.summary(session_id)
        if summary is None:
            raise KeyError(str(session_id))
        provider = self.registry.plugin_for_session(session_id).memory
        if provider is None or not provider.enabled(summary.working_directory):
            return MemoryStatus(False, 0)
        return MemoryStatus(True, provider.item_count(session_id))

    def snapshot(self, session_id: SessionId) -> HarnessMemorySnapshot:
        provider = self._provider(session_id)
        return provider.snapshot(session_id)

    def document(
        self,
        session_id: SessionId,
        path: str | None,
        stem: str | None,
    ) -> MemoryDocument:
        return self._provider(session_id).document(path, stem)

    def _provider(self, session_id: SessionId) -> HarnessMemory:
        summary = self.queries.summary(session_id)
        if summary is None:
            raise KeyError(str(session_id))
        provider = self.registry.plugin_for_session(session_id).memory
        if provider is None or not provider.enabled(summary.working_directory):
            raise ValueError("memory is not enabled for this session")
        return provider
