"""One typed and consistent client view of a session."""

from __future__ import annotations

from dataclasses import dataclass, field

from api.sessiondata.models.entry import (
    AssignmentFinishedBodyResponse,
    AssignmentStartedBodyResponse,
    CompactionFinishedBodyResponse,
    CompactionStartedBodyResponse,
    EntryResponse,
    MessageBodyResponse,
    PlanProposedBodyResponse,
    PlanResolvedBodyResponse,
    QuestionAnsweredBodyResponse,
    QuestionAskedBodyResponse,
    QuestionAnswerResponse,
    QuestionResponse,
    ShellBackgroundedBodyResponse,
    ShellFinishedBodyResponse,
    ShellOutputBodyResponse,
    ShellStartedBodyResponse,
    SkillFinishedBodyResponse,
    SkillStartedBodyResponse,
    TurnFinishedBodyResponse,
)
from api.sessiondata.models.session_data import ActorResponse, SessionDataResponse


@dataclass
class ShellState:
    shell_id: str
    actor_id: str
    turn_id: str | None
    command: str
    execution: str
    started_cursor: int
    output: str = ""
    status: str = ""
    state: str | None = None
    exit_code: int | None = None
    backgrounded: bool = False
    entry_ids: list[str] = field(default_factory=list)


@dataclass
class AssignmentState:
    assignment_id: str
    owner_actor_id: str
    actor_id: str | None
    turn_id: str | None
    assigned_actor_name: str | None
    requested_prompt: str | None
    started_cursor: int
    state: str | None = None
    result: str = ""
    finished_cursor: int | None = None


@dataclass
class SkillState:
    skill_id: str
    actor_id: str
    turn_id: str | None
    name: str
    arguments: str
    started_cursor: int
    state: str | None = None
    result: str = ""


@dataclass
class QuestionState:
    attention_id: str
    actor_id: str
    turn_id: str | None
    questions: tuple[QuestionResponse, ...]
    asked_cursor: int
    answers: tuple[QuestionAnswerResponse, ...] | None = None
    feedback: str | None = None

    @property
    def pending(self) -> bool:
        return self.answers is None


@dataclass
class PlanState:
    attention_id: str
    actor_id: str
    turn_id: str | None
    text: str
    proposed_cursor: int
    state: str | None = None
    feedback: str | None = None
    edited: bool = False

    @property
    def pending(self) -> bool:
        return self.state is None


@dataclass
class CompactionState:
    actor_id: str
    turn_id: str | None
    started_cursor: int
    before_tokens: int | None
    after_tokens: int | None = None
    finished_cursor: int | None = None

    @property
    def finished(self) -> bool:
        return self.finished_cursor is not None


@dataclass(frozen=True)
class SessionSnapshot:
    data: SessionDataResponse
    entries: tuple[EntryResponse, ...]

    @property
    def cursor(self) -> int:
        return self.data.cursor

    @property
    def session_id(self) -> str:
        return self.data.session.session_id

    def actor(self, actor_id: str) -> ActorResponse:
        found = [actor for actor in self.data.actors if actor.actor_id == actor_id]
        if len(found) != 1:
            raise LookupError(f"actor {actor_id!r} has {len(found)} matches")
        return found[0]

    def lead(self) -> ActorResponse:
        return self.actor(self.data.session.lead_actor_id)

    def messages(
        self,
        *,
        actor_id: str | None = None,
        role: str | None = None,
        phase: str | None = None,
    ) -> tuple[EntryResponse, ...]:
        return tuple(
            entry
            for entry in self.entries
            if (actor_id is None or entry.actor_id == actor_id)
            and isinstance(entry.body, MessageBodyResponse)
            and (role is None or entry.body.role == role)
            and (phase is None or entry.body.phase == phase)
        )

    def shells(self, *, actor_id: str | None = None) -> tuple[ShellState, ...]:
        return _shells(self.entries, actor_id=actor_id)

    def assignments(self) -> tuple[AssignmentState, ...]:
        assignments = _assignments(self.entries)
        for assignment in assignments:
            if (
                assignment.actor_id in (None, assignment.owner_actor_id)
                and assignment.assigned_actor_name
            ):
                candidates = [
                    actor
                    for actor in self.data.actors
                    if actor.parent_actor_id == assignment.owner_actor_id
                    and actor.name == assignment.assigned_actor_name
                ]
                if len(candidates) == 1:
                    assignment.actor_id = candidates[0].actor_id
            if (
                assignment.state is None
                or assignment.result
                or assignment.actor_id is None
            ):
                continue
            final_messages = [
                entry
                for entry in self.entries
                if entry.cursor > assignment.started_cursor
                and (
                    assignment.finished_cursor is None
                    or entry.cursor < assignment.finished_cursor
                )
                and entry.actor_id == assignment.actor_id
                and isinstance(entry.body, MessageBodyResponse)
                and entry.body.recipient_actor_id == assignment.owner_actor_id
                and entry.body.role in ("assistant", "peer")
                and entry.body.content.text.strip()
            ]
            if final_messages:
                final_body = final_messages[-1].body
                if isinstance(final_body, MessageBodyResponse):
                    assignment.result = final_body.content.text.strip()
        return assignments

    def skills(self) -> tuple[SkillState, ...]:
        return _skills(self.entries)

    def questions(self) -> tuple[QuestionState, ...]:
        return _questions(self.entries)

    def plans(self) -> tuple[PlanState, ...]:
        return _plans(self.entries)

    def compactions(self) -> tuple[CompactionState, ...]:
        return _compactions(self.entries)

    def turn_state(self, turn_id: str) -> str | None:
        states = [
            entry.body.state
            for entry in self.entries
            if entry.turn_id == turn_id and isinstance(entry.body, TurnFinishedBodyResponse)
        ]
        if len(states) > 1:
            raise LookupError(f"turn {turn_id!r} has {len(states)} finished states")
        return states[0] if states else None


SHELL_BODIES = (
    ShellStartedBodyResponse,
    ShellOutputBodyResponse,
    ShellBackgroundedBodyResponse,
    ShellFinishedBodyResponse,
)


def _shells(
    entries: tuple[EntryResponse, ...], *, actor_id: str | None
) -> tuple[ShellState, ...]:
    folded: dict[str, ShellState] = {}
    for entry in entries:
        body = entry.body
        if actor_id is not None and entry.actor_id != actor_id:
            continue
        if not isinstance(body, SHELL_BODIES):
            continue
        if isinstance(body, ShellStartedBodyResponse):
            folded[body.shell_id] = ShellState(
                shell_id=body.shell_id,
                actor_id=entry.actor_id,
                turn_id=entry.turn_id,
                command=body.command.text,
                execution=body.execution,
                started_cursor=entry.cursor,
            )
        found = folded.get(body.shell_id)
        if found is None:
            continue
        found.entry_ids.append(entry.entry_id)
        if isinstance(body, ShellOutputBodyResponse):
            current = found.status if body.stream == "status" else found.output
            value = body.content.text if body.mode == "replace" else current + body.content.text
            if body.stream == "status":
                found.status = value
            else:
                found.output = value
        elif isinstance(body, ShellBackgroundedBodyResponse):
            found.backgrounded = True
        elif isinstance(body, ShellFinishedBodyResponse):
            found.state = body.state
            found.exit_code = body.exit_code
            if body.result is not None and body.result.text:
                found.output = body.result.text
    return tuple(folded.values())


def _assignments(entries: tuple[EntryResponse, ...]) -> tuple[AssignmentState, ...]:
    folded: dict[str, AssignmentState] = {}
    for entry in entries:
        body = entry.body
        if isinstance(body, AssignmentStartedBodyResponse):
            owner_actor_id = entry.parent_actor_id or entry.actor_id
            folded[body.assignment_id] = AssignmentState(
                assignment_id=body.assignment_id,
                owner_actor_id=owner_actor_id,
                actor_id=(
                    entry.actor_id if entry.parent_actor_id is not None else None
                ),
                turn_id=entry.turn_id,
                assigned_actor_name=body.assigned_actor_name,
                requested_prompt=(
                    body.prompt.text if body.prompt is not None else None
                ),
                started_cursor=entry.cursor,
            )
        elif (
            isinstance(body, MessageBodyResponse)
            and body.role == "parent"
            and body.phase == "prompt"
            and entry.parent_actor_id is not None
        ):
            candidates = [
                item
                for item in folded.values()
                if item.actor_id is None
                and item.owner_actor_id == entry.parent_actor_id
                and item.started_cursor < entry.cursor
                and item.state is None
                and item.requested_prompt is not None
                and item.requested_prompt.strip() == body.content.text.strip()
            ]
            if len(candidates) == 1:
                candidates[0].actor_id = entry.actor_id
        elif isinstance(body, AssignmentFinishedBodyResponse):
            found = folded.get(body.assignment_id)
            if found is None:
                found = AssignmentState(
                    assignment_id=body.assignment_id,
                    owner_actor_id=entry.parent_actor_id or entry.actor_id,
                    actor_id=entry.actor_id,
                    turn_id=entry.turn_id,
                    assigned_actor_name=None,
                    requested_prompt=None,
                    started_cursor=entry.cursor,
                )
                folded[body.assignment_id] = found
            else:
                found.actor_id = entry.actor_id
            found.state = body.state
            found.result = body.result.text if body.result is not None else ""
            found.finished_cursor = entry.cursor
    return tuple(folded.values())


def _skills(entries: tuple[EntryResponse, ...]) -> tuple[SkillState, ...]:
    folded: dict[str, SkillState] = {}
    for entry in entries:
        body = entry.body
        if isinstance(body, SkillStartedBodyResponse):
            folded[body.skill_id] = SkillState(
                skill_id=body.skill_id,
                actor_id=entry.actor_id,
                turn_id=entry.turn_id,
                name=body.name,
                arguments=body.arguments.text if body.arguments is not None else "",
                started_cursor=entry.cursor,
            )
        elif isinstance(body, SkillFinishedBodyResponse):
            found = folded.get(body.skill_id)
            if found is None:
                continue
            found.state = body.state
            found.result = body.result.text if body.result is not None else ""
    return tuple(folded.values())


def _questions(entries: tuple[EntryResponse, ...]) -> tuple[QuestionState, ...]:
    folded: dict[str, QuestionState] = {}
    for entry in entries:
        body = entry.body
        if isinstance(body, QuestionAskedBodyResponse):
            folded[body.attention_id] = QuestionState(
                attention_id=body.attention_id,
                actor_id=entry.actor_id,
                turn_id=entry.turn_id,
                questions=body.questions,
                asked_cursor=entry.cursor,
            )
        elif isinstance(body, QuestionAnsweredBodyResponse):
            found = folded.get(body.attention_id)
            if found is None:
                continue
            found.answers = body.answers
            found.feedback = body.feedback
    return tuple(folded.values())


def _plans(entries: tuple[EntryResponse, ...]) -> tuple[PlanState, ...]:
    folded: dict[str, PlanState] = {}
    for entry in entries:
        body = entry.body
        if isinstance(body, PlanProposedBodyResponse):
            folded[body.attention_id] = PlanState(
                attention_id=body.attention_id,
                actor_id=entry.actor_id,
                turn_id=entry.turn_id,
                text=body.plan.text,
                proposed_cursor=entry.cursor,
            )
        elif isinstance(body, PlanResolvedBodyResponse):
            found = folded.get(body.attention_id)
            if found is None:
                continue
            # Claude can report a generic tool rejection after the control
            # already recorded the person's feedback. That late, weaker
            # observation must not erase the explicit decision.
            if (
                found.state == "changes_requested"
                and found.feedback
                and body.state == "rejected"
                and not body.feedback
            ):
                continue
            found.state = body.state
            found.feedback = body.feedback
            found.edited = body.edited
    return tuple(folded.values())


def _compactions(entries: tuple[EntryResponse, ...]) -> tuple[CompactionState, ...]:
    found: list[CompactionState] = []
    open_by_actor: dict[str, CompactionState] = {}
    for entry in entries:
        body = entry.body
        if isinstance(body, CompactionStartedBodyResponse):
            started = CompactionState(
                actor_id=entry.actor_id,
                turn_id=entry.turn_id,
                started_cursor=entry.cursor,
                before_tokens=body.before_tokens,
            )
            found.append(started)
            open_by_actor[entry.actor_id] = started
        elif isinstance(body, CompactionFinishedBodyResponse):
            open_state = open_by_actor.get(entry.actor_id)
            if open_state is None:
                continue
            del open_by_actor[entry.actor_id]
            open_state.before_tokens = body.before_tokens
            open_state.after_tokens = body.after_tokens
            open_state.finished_cursor = entry.cursor
    return tuple(found)
