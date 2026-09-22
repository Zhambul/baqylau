# Copyright (c) 2026 Zhambyl Yermagambet
"""Run core reactions against actual mixed storage without an extension worker."""

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import Mock

from domain.event_base import EventPayload
from engine.react.loop import ReactionLoop
from extensions.mapper.core_events import public_candidate
from repository.impl.sqlite.interpretations import SqliteInterpretationRepository
from repository.impl.sqlite.session_data import SqliteSessionDataRepository
from tests import (
    canonical_sessiondata_fixtures as payloads,
    canonical_sessiondata_folding as folding,
    canonical_sessiondata_loop_models as probes,
    canonical_sessiondata_loop_support as loops,
)
from tests.extension_host import interpretation_snapshot_fixture as storage


@dataclass(frozen=True)
class ReactionCase:
    """Keep real stores and visible core call evidence together."""

    loop: ReactionLoop
    view: SqliteSessionDataRepository
    store: SqliteInterpretationRepository
    reaction: probes.RecordingReaction
    listener: Mock
    audit: probes.RecordingAudit


def installed(path: Path, *, core: bool = True) -> ReactionCase:
    """Create an independent core consumer, with optional startup facts.

    Returns:
        Production readers, writers, and loop with controlled side effects.

    """
    reaction = probes.RecordingReaction()
    listener = Mock()
    loop, view, audit = loops.loop_over(
        path, payloads.alive() if core else (), reaction=reaction, listener=listener,
    )
    view.sqlite_database.changes = loop.dependencies.changes
    return ReactionCase(loop, view, SqliteInterpretationRepository(view.sqlite_database), reaction, listener, audit)


def append_extensions(case: ReactionCase, count: int = 1) -> None:
    """Seed typed read fixtures without claiming a source interpretation."""
    head = case.store.current_fact_page(0, 1).head
    positions = range(head, head + count)
    facts = tuple(storage.fact(f"extension-{index}") for index in positions)
    storage.seed(case.store, facts)


def append_core(case: ReactionCase, payload: EventPayload) -> None:
    """Use the production core codec for one more read fixture."""
    cursor = case.store.current_fact_page(0, 1).head + 1
    event = folding.committed(payload, cursor=cursor)
    storage.seed(case.store, (public_candidate(event),))
