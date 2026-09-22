# Copyright (c) 2026 Zhambyl Yermagambet
"""Define the services that the reaction loop uses."""

from __future__ import annotations

from dataclasses import dataclass, field

from audit.failures import ErrorRecorder
from core.change_signal import ChangeSignal
from engine.sessiondata.contract import AppliedActorListener, SessionDataWriter, SessionEntryWriter
from harness.contract import CanonicalEventReaction, HarnessReactorContext, HarnessReactorProvider
from repository.contract.interpretations import CanonicalFactReader
from repository.contract.session_data import SessionDataRepository


@dataclass(frozen=True)
class ReactionLoopDependencies:
    """Contain the stores and services that the reaction loop uses."""

    canonical_fact_reader: CanonicalFactReader
    session_data_repository: SessionDataRepository
    reactions: tuple[CanonicalEventReaction, ...]
    session_entry_writer: SessionEntryWriter
    writers: tuple[SessionDataWriter, ...]
    listeners: tuple[AppliedActorListener, ...]
    harness_registry: HarnessReactorProvider
    harness_reactor_context: HarnessReactorContext | None
    audit_recorder: ErrorRecorder
    changes: ChangeSignal = field(default_factory=ChangeSignal)
