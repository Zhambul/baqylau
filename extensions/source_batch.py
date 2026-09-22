# Copyright (c) 2026 Zhambyl Yermagambet
"""Prepare watches, release old scopes, and drain sources under one retained runtime."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from extensions.models.source_processing import SourcePolicy, SourceScopeKey, SourceScopePlan, next_deadline
from extensions.source_planner import SourcePlanner
from extensions.source_processing_contract import ExtensionSourceBatch, ExtensionSourceWatches
from extensions.source_reader import SourceReader
from extensions.source_resources import SourceCallbacks, SourcePlans, SourceServices
from extensions.source_selection import SourceBatchContext, select_readers, select_scopes


@dataclass(frozen=True)
class SelectedSourceBatch(ExtensionSourceBatch):
    """Never retain this object after the outer registry context exits."""

    context: SourceBatchContext
    services: SourceServices
    callbacks: SourceCallbacks
    plans: SourcePlans
    policy: SourcePolicy

    def read_sources(
        self, watches: ExtensionSourceWatches, stopped: Callable[[], bool], *, refresh_plans: bool,
    ) -> float | None:
        """Register the complete selected watch set before any original read.

        Returns:
            An absolute deadline for actual continuation or retry, or no idle timer.

        """
        readers = select_readers(self.context, self.services, self.callbacks, self.policy)
        scopes = self.context.scopes
        selected = tuple(
            key for reader in readers.values() for key in select_scopes(reader, scopes)
        )
        self._retire(readers, selected, stopped)
        self._prepare(readers, selected, stopped, refresh=refresh_plans)
        watches.watch_sources(self._watch_paths(selected))
        self._read(readers, selected, stopped, notified=refresh_plans)
        due = next_deadline(tuple(self.plans.scopes.values()))
        minimum = self.callbacks.clock() + self.policy.continuation_seconds
        return None if due is None else max(due, minimum)

    def _retire(
        self, readers: Mapping[str, SourceReader], selected: tuple[SourceScopeKey, ...],
        stopped: Callable[[], bool],
    ) -> None:
        removed = tuple(
            plan for plan in self.plans.scopes.values() if plan.key not in selected or plan.retiring
        )
        for previous in removed:
            if stopped():
                return
            reader = readers.get(previous.key.extension_id)
            remaining = None if reader is None else SourcePlanner(reader).retire(previous)
            if remaining is None:
                self.plans.scopes.pop(previous.key)
            else:
                self.plans.scopes[previous.key] = remaining

    def _prepare(
        self, readers: Mapping[str, SourceReader], selected: tuple[SourceScopeKey, ...],
        stopped: Callable[[], bool],
        *, refresh: bool,
    ) -> None:
        for key in selected:
            if stopped():
                return
            previous = self.plans.scopes.get(key)
            if previous is not None and previous.retiring:
                continue
            self.plans.scopes[key] = SourcePlanner(readers[key.extension_id]).prepare(
                SourceScopePlan(key) if previous is None else previous, refresh=refresh or previous is None,
            )

    def _watch_paths(self, selected: tuple[SourceScopeKey, ...]) -> frozenset[Path]:
        return frozenset(
            Path(path) for plan in self.plans.scopes.values()
            if plan.key in selected for path in plan.watch_paths()
        )

    def _read(
        self, readers: Mapping[str, SourceReader], selected: tuple[SourceScopeKey, ...],
        stopped: Callable[[], bool],
        *, notified: bool,
    ) -> None:
        for key in selected:
            if stopped():
                return
            plan = self.plans.scopes.get(key)
            if plan is None or plan.retiring or plan.retry_at is not None:
                continue
            self.plans.scopes[key] = replace(plan, sources=tuple(
                readers[key.extension_id].read(key, progress, stopped, notified=notified) for progress in plan.sources
            ))
