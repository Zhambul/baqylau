# Copyright (c) 2026 Zhambyl Yermagambet
"""Data objects for E2E work requests and results."""

from __future__ import annotations

from dataclasses import dataclass

from api.controls.models.attachment_reference import AttachmentReferenceBody
from sdk.client import SessionRef
from tests.e2e.testkit.references import TurnRef, WorkerKind, WorkRef


@dataclass(frozen=True)
class StartedWork:
    """Represent started work."""

    session: SessionRef
    work: WorkRef


@dataclass(frozen=True)
class WorkRequest:
    """Represent one work request."""

    name: str
    prompt: str
    worker_kind: WorkerKind = WorkerKind.SUBAGENT
    # Whether the lead leaves the subagent to run and ends its own turn. Every
    # harness offers both, but by a different gesture, so the scenario states
    # the wanted behaviour and each adapter names its own tool argument.
    background: bool = True
    attachments: tuple[AttachmentReferenceBody, ...] = ()
    named: bool = False
    exact_actor_name: str | None = None
    exact_prompt: str | None = None


@dataclass(frozen=True)
class StartedParallelWork:
    """Represent started parallel work."""

    session: SessionRef
    request_turn: TurnRef
    works: tuple[tuple[str, WorkRef], ...]
