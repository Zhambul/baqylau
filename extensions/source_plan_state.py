# Copyright (c) 2026 Zhambyl Yermagambet
"""Replace complete source plans without resetting accepted read schedules."""

from dataclasses import replace

from baqylau_extension_api.models.source_results import SourcePlan
from baqylau_extension_api.models.sources import SourceDescriptor

from extensions.models.source_processing import SourceProgress, SourceScopePlan


def replace_plan(previous: SourceScopePlan, selected: SourcePlan) -> SourceScopePlan:
    """Keep matching descriptors and queue release for removed identities.

    Returns:
        Checked data only, with no worker or registry resource retained.

    """
    identities = frozenset(source.source_identity for source in selected.sources)
    removed = tuple(
        progress.source.source_identity for progress in previous.sources
        if progress.source.source_identity not in identities
    )
    progress = tuple(_selected_progress(source, previous.sources) for source in selected.sources)
    return replace(previous, sources=progress, release_pending=removed, retry_at=None)


def requires_plan(previous: SourceScopePlan, now: float, *, refresh: bool) -> bool:
    """Retry failed plans and source reads without resetting healthy timer plans on every deadline.

    Returns:
        True when a describe call is needed at this boundary.

    """
    if previous.retry_at is not None:
        return previous.retry_at <= now
    return refresh or any(
        progress.retry_at is not None and progress.retry_at <= now for progress in previous.sources
    )


def _selected_progress(source: SourceDescriptor, previous: tuple[SourceProgress, ...]) -> SourceProgress:
    progress = next((old for old in previous if old.source == source), SourceProgress(
        source=source, next_due_at=source.next_due_at,
    ))
    # A successful describe must retain a file notice whose earlier plan call failed.
    return replace(progress, read_once=False) if source.watch_paths else progress
