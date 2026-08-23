"""Named immutable references carried by one scenario."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Generic, TypeVar

from api.application.models.harnesses.harness_catalog_response import HarnessCatalogResponse
from api.application.models.harnesses.harness_description_response import (
    HarnessDescriptionResponse,
)
from api.application.models.insights.application_insights_response import (
    ApplicationInsightsResponse,
)
from api.application.models.files.upload_response import UploadResponse
from api.application.models.resume.resumable_session_response import (
    ResumableSessionResponse,
)
from sdk.client import ActionReceipt, SessionRef

T = TypeVar("T")


class References(Generic[T]):
    def __init__(self, noun: str) -> None:
        self.noun = noun
        self._items: dict[str, T] = {}

    def bind(self, name: str, value: T) -> T:
        if name in self._items:
            raise AssertionError(f"{self.noun} name {name!r} is already bound")
        self._items[name] = value
        return value

    def replace(self, name: str, value: T) -> T:
        if name not in self._items:
            raise AssertionError(
                f"unknown {self.noun} {name!r}; available names: {sorted(self._items)}"
            )
        self._items[name] = value
        return value

    def get(self, name: str) -> T:
        try:
            return self._items[name]
        except KeyError as error:
            raise AssertionError(
                f"unknown {self.noun} {name!r}; available names: {sorted(self._items)}"
            ) from error

    def values(self) -> tuple[T, ...]:
        return tuple(self._items.values())


@dataclass(frozen=True)
class SessionSpec:
    harness: str
    model: str
    effort: str


@dataclass(frozen=True)
class TurnRef:
    session: SessionRef
    prompt: str
    cursor_before: int
    expected_prompt_count: int
    actor_id: str | None = None
    turn_id: str | None = None
    prompt_cursor: int | None = None
    prompt_message_id: str | None = None
    completion_after_cursor: int | None = None

    def resumed_after(self, cursor: int) -> TurnRef:
        """Require the next completion to be newer than one control action."""
        return replace(self, completion_after_cursor=cursor)


@dataclass(frozen=True)
class ShellRef:
    session: SessionRef
    shell_id: str


@dataclass(frozen=True)
class ActorRef:
    session: SessionRef
    actor_id: str


@dataclass(frozen=True)
class AssignmentRef:
    session: SessionRef
    assignment_id: str


@dataclass(frozen=True)
class FileOperationRef:
    session: SessionRef
    entry_id: str


@dataclass(frozen=True)
class SkillRef:
    session: SessionRef
    skill_id: str


@dataclass(frozen=True)
class QuestionRef:
    session: SessionRef
    attention_id: str
    question_id: str
    turn_name: str


@dataclass(frozen=True)
class PlanRef:
    session: SessionRef
    attention_id: str
    turn_name: str


@dataclass(frozen=True)
class TaskRef:
    session: SessionRef
    task_id: str


@dataclass(frozen=True)
class CompactionRef:
    session: SessionRef
    actor_id: str
    started_cursor: int


SessionSpecs = References[SessionSpec]
Sessions = References[SessionRef]
Turns = References[TurnRef]
Shells = References[ShellRef]
Actors = References[ActorRef]
Assignments = References[AssignmentRef]
FileOperations = References[FileOperationRef]
Skills = References[SkillRef]
Questions = References[QuestionRef]
Plans = References[PlanRef]
Tasks = References[TaskRef]
Compactions = References[CompactionRef]
Controls = References[ActionReceipt]
HarnessLists = References[tuple[HarnessDescriptionResponse, ...]]
HarnessCatalogs = References[HarnessCatalogResponse]
InsightsSnapshots = References[ApplicationInsightsResponse]
ResumableLists = References[tuple[ResumableSessionResponse, ...]]
StagedAttachments = References[UploadResponse]
