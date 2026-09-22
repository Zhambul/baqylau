# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep source scheduling state separate from borrowed worker capabilities."""

from dataclasses import dataclass
from typing import Annotated

from baqylau_extension_api.models import base, source_results, sources
from baqylau_extension_api.models.scopes import ExtensionScope
from baqylau_extension_api.runtime.models import RequestTimeout
from pydantic import Field

from extensions.models.workers import WorkerPolicy


class SourcePolicy(base.WireModel):
    """Bound each source pass; final release bounds still need measurement."""

    batches_per_source: Annotated[int, Field(ge=1, le=100)] = 4
    retry_seconds: Annotated[float, Field(gt=0)] = 1.0
    continuation_seconds: Annotated[float, Field(gt=0)] = 0.01
    call_seconds: RequestTimeout = WorkerPolicy().request_seconds


@dataclass(frozen=True)
class SourceScopeKey:
    """Name one owner's complete host-selected scope."""

    extension_id: str
    scope: ExtensionScope


@dataclass(frozen=True)
class SourceProgress:
    """Keep planned descriptors and read deadlines, not worker objects."""

    source: sources.SourceDescriptor
    read_once: bool = False
    has_more: bool = False
    next_due_at: float | None = None
    retry_at: float | None = None

    def ready(self, now: float, *, notified: bool) -> bool:
        """Select initial, file-notified, continuing, or due work.

        Returns:
            True when this source can need a read during the current source notice.

        """
        if self.retry_at is not None:
            return self.retry_at <= now
        if self.source.watch_paths and (notified or not self.read_once):
            return True
        if self.has_more:
            return True
        if self.next_due_at is not None:
            return self.next_due_at <= now
        return not self.read_once

    def deadline(self) -> float | None:
        """Return the retry deadline before any regular deadline.

        Returns:
            The next allowed read time, if one is scheduled.

        """
        return self.next_due_at if self.retry_at is None else self.retry_at


@dataclass(frozen=True)
class SourceScopePlan:
    """Retain only checked plan documents and scheduling state for one scope."""

    key: SourceScopeKey
    sources: tuple[SourceProgress, ...] = ()
    retry_at: float | None = None
    release_pending: tuple[str, ...] = ()
    retiring: bool = False

    def deadlines(self) -> tuple[float | None, ...]:
        """Do not use an expired source deadline while plan cleanup waits for retry.

        Returns:
            The next plan retry or its independent read deadlines.

        """
        if self.retry_at is None:
            return tuple(progress.deadline() for progress in self.sources)
        return (self.retry_at,)

    def watch_paths(self) -> frozenset[str]:
        """Remove native watch requests while a whole scope is being released.

        Returns:
            The complete selected paths, with no filesystem I/O.

        """
        if self.retiring:
            return frozenset()
        return frozenset(path for progress in self.sources for path in progress.source.watch_paths)


@dataclass(frozen=True)
class SourceReply:
    """Keep the exact request beside a successful or failed source reply."""

    request: sources.SourceReadRequest
    response: source_results.SourceReadResult


def next_deadline(plans: tuple[SourceScopePlan, ...]) -> float | None:
    """Select the earliest actual work or retry deadline.

    Returns:
        No deadline for a fully idle file-driven selection.

    """
    deadlines = (deadline for plan in plans for deadline in plan.deadlines() if deadline is not None)
    return min(deadlines, default=None)
