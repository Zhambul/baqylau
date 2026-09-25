# Copyright (c) 2026 Zhambyl Yermagambet
"""One typed and consistent client view of a session."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import quote

from api.sessiondata.models import entry as entry_models
from api.sessiondata.models.session_data import SessionDataResponse
from sdk import (
    state_assignment_resolution,
    state_assignments,
    state_compactions,
    state_models,
    state_plans,
    state_questions,
    state_shells,
    state_skills,
)

AssignmentState = state_models.AssignmentState
CompactionState = state_models.CompactionState
PlanState = state_models.PlanState
QuestionState = state_models.QuestionState
ShellState = state_models.ShellState
SkillState = state_models.SkillState

if TYPE_CHECKING:

    from api.sessiondata.models.actor import ActorResponse


@dataclass(frozen=True)
class _SessionSnapshotData:
    """Store data that describes one session snapshot."""

    session_data: SessionDataResponse
    entries: tuple[entry_models.EntryResponse, ...]


@dataclass(frozen=True)
class SessionRef:
    """Identify one session in client operations."""

    session_id: str

    @property
    def path_segment(self) -> str:
        """The URL-safe session identifier."""
        return quote(self.session_id, safe="")


class _SessionSnapshotIdentity(_SessionSnapshotData):
    """Provide snapshot identity queries."""

    @property
    def cursor(self) -> int:
        """Current session cursor."""
        return self.session_data.cursor

    @property
    def session_id(self) -> str:
        """Session ID."""
        return self.session_data.session.session_id

    @property
    def session_reference(self) -> SessionRef:
        """The stable reference for this snapshot session."""
        return SessionRef(self.session_id)

    def actor(self, actor_id: str) -> ActorResponse:
        """Return the actor.

        Returns:
            Actor.

        Raises:
            LookupError: If a required item does not exist.

        """
        found = [actor for actor in self.session_data.actors if actor.actor_id == actor_id]
        if len(found) != 1:
            message = f"actor {actor_id!r} has {len(found)} matches"
            raise LookupError(message)
        return found[0]

    def lead(self) -> ActorResponse:
        """Return the lead.

        Returns:
            Lead.

        """
        return self.actor(self.session_data.session.lead_actor_id)


class _SessionSnapshotEntries(_SessionSnapshotData):
    """Provide snapshot entry queries."""

    @property
    def entry_cursor(self) -> int:
        """The newest entry cursor, or 0.

        Entry cursors are their own counter, so a mark that is later compared
        with an entry cursor must come from here and not from `cursor`.
        """
        return max((entry.cursor for entry in self.entries), default=0)

    def messages(
        self,
        *,
        actor_id: str | None = None,
        role: str | None = None,
        phase: str | None = None,
    ) -> tuple[entry_models.EntryResponse, ...]:
        """Return the messages.

        Returns:
            Messages.

        """
        return tuple(
            entry
            for entry in self.entries
            if (actor_id is None or entry.actor_id == actor_id)
            and isinstance(entry.body, entry_models.MessageBodyResponse)
            and (role is None or entry.body.role == role)
            and (phase is None or entry.body.phase == phase)
        )

    def shells(self, *, actor_id: str | None = None) -> tuple[ShellState, ...]:
        """Return the shells.

        Returns:
            Shells.

        """
        return state_shells.shells(self.entries, actor_id=actor_id)


class _SessionSnapshotWork(_SessionSnapshotData):
    """Provide snapshot work state queries."""

    def assignments(self) -> tuple[AssignmentState, ...]:
        """Return the assignments.

        Returns:
            Assignments.

        """
        assignments = state_assignments.assignments(self.entries)
        for assignment in assignments:
            assignment.actor_id = state_assignment_resolution.actor_id(assignment, self.session_data.actors)
            result = state_assignment_resolution.result(assignment, self.entries)
            if result is not None:
                assignment.result = result
        return assignments

    def skills(self) -> tuple[SkillState, ...]:
        """Return the skills.

        Returns:
            Skills.

        """
        return state_skills.skills(self.entries)

    def questions(self) -> tuple[QuestionState, ...]:
        """Return the questions.

        Returns:
            Questions.

        """
        return state_questions.questions(self.entries)

    def plans(self) -> tuple[PlanState, ...]:
        """Return the plans.

        Returns:
            Plans.

        """
        return state_plans.plans(self.entries)

    def compactions(self) -> tuple[CompactionState, ...]:
        """Return the compactions.

        Returns:
            Compactions.

        """
        return state_compactions.compactions(self.entries)

    def turn_state(self, turn_id: str) -> str | None:
        """Return the turn state.

        Returns:
            Turn state.

        Raises:
            LookupError: If a required item does not exist.

        """
        states = [
            entry.body.state
            for entry in self.entries
            if entry.turn_id == turn_id and isinstance(entry.body, entry_models.TurnFinishedBodyResponse)
        ]
        if len(states) > 1:
            message = f"turn {turn_id!r} has {len(states)} finished states"
            raise LookupError(message)
        return states[0] if states else None


class SessionSnapshot(
    _SessionSnapshotIdentity,
    _SessionSnapshotEntries,
    _SessionSnapshotWork,
):
    """Represent one typed and consistent session snapshot."""
