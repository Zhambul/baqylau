# Copyright (c) 2026 Zhambyl Yermagambet
"""Name candidate projection generations, their comparison, and their switch."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel


class GenerationState(StrEnum):
    """Show where one projection generation is in its rebuild and switch."""

    BUILDING = "building"
    MIGRATING = "migrating"
    READY = "ready"
    ACTIVE = "active"
    RETIRED = "retired"
    FAILED = "failed"


class GenerationComparison(BaseModel):
    """Count the live and candidate rows of one owner and the rows with equal content."""

    live_records: int
    candidate_records: int
    equal_records: int
    live_entries: int
    candidate_entries: int
    equal_entries: int


class ProjectionSwitchError(RuntimeError):
    """Reject a switch to a generation that is absent, not built, or behind the live one."""


@dataclass(frozen=True)
class ProjectionGeneration:
    """Keep one generation's owner, history, state, and comparison."""

    generation: str
    owner: str
    history_revision: str
    state: GenerationState
    comparison: GenerationComparison | None = None
    diagnostic: str | None = None


class ProjectionGenerationRepository(Protocol):
    """Own candidate generations and the atomic switch of one owner's live generation."""

    def active_generation(self, owner: str) -> str:
        """Read one owner's active projection generation."""
        ...

    def create(self, owner: str, history_revision: str) -> ProjectionGeneration:
        """Start one empty candidate generation for an owner."""
        ...

    def read(self, generation: str) -> ProjectionGeneration | None:
        """Read one stored generation."""
        ...

    def generations_in_state(self, generation_state: GenerationState) -> tuple[ProjectionGeneration, ...]:
        """Read the generations in one state, oldest first."""
        ...

    def settle(self, generation: str, generation_state: GenerationState, diagnostic: str | None = None) -> None:
        """Record a built or failed candidate and its comparison with the live generation."""
        ...

    def resume(self, generation: str) -> ProjectionGeneration:
        """Let a retired generation catch up from its own cursors before a switch back to it."""
        ...

    def switch(self, generation: str) -> ProjectionGeneration:
        """Make a ready or retired generation live, and keep the previous live one for recovery."""
        ...
