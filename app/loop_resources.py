# Copyright (c) 2026 Zhambyl Yermagambet
"""Group dependencies for interpreter and reaction loops."""

from collections.abc import Mapping
from dataclasses import dataclass

from audit.recorder import AuditRecorder
from engine.sessiondata import contract as session_contract
from harness import contract
from harness.models.interrupts import InterruptRegistry
from harness.registry import HarnessRegistry
from harness.services.controls import HarnessControlService
from repository.contract import facts, interpretations, session_data, sessions, shell_output
from terminal.adapter import TerminalAdapter


@dataclass(frozen=True)
class InterpreterSources:
    """Hold source repositories for canonical interpretation."""

    session_repository: sessions.SessionRepository
    harness_registry: HarnessRegistry
    raw_event_repository: facts.RawEventRepository
    shell_output_repository: shell_output.ShellOutputRepository
    canonical_event_repository: facts.CanonicalEventRepository


@dataclass(frozen=True)
class InterpreterServices:
    """Hold services used during canonical interpretation."""

    translators: Mapping[str, contract.CoreTranslator]
    input_reactions: tuple[contract.CanonicalEventReaction, ...]
    audit_recorder: AuditRecorder
    interrupt_registry: InterruptRegistry
    terminal_adapter: TerminalAdapter


@dataclass(frozen=True)
class ReactionData:
    """Hold canonical and aggregate writers for the reaction loop."""

    canonical_fact_reader: interpretations.CanonicalFactReader
    session_data_repository: session_data.SessionDataRepository
    event_reactions: tuple[contract.CanonicalEventReaction, ...]
    entry_writer: session_contract.SessionEntryWriter
    session_data_writers: tuple[session_contract.SessionDataWriter, ...]


@dataclass(frozen=True)
class ReactionServices:
    """Hold services that observe or support the reaction loop."""

    applied_listeners: tuple[session_contract.AppliedActorListener, ...]
    harness_registry: HarnessRegistry
    control_service: HarnessControlService
    audit_recorder: AuditRecorder
