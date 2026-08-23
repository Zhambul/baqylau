"""Named immutable references carried by one scenario."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

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
    turn_id: str | None = None
    prompt_cursor: int | None = None


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


SessionSpecs = References[SessionSpec]
Sessions = References[SessionRef]
Turns = References[TurnRef]
Shells = References[ShellRef]
Actors = References[ActorRef]
Assignments = References[AssignmentRef]
FileOperations = References[FileOperationRef]
Controls = References[ActionReceipt]
