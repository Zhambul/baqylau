# Copyright (c) 2026 Zhambyl Yermagambet
"""Which turn is open, per actor.

Claude Code announces no turn boundary of its own: its Stop hook says a turn
ENDED and nothing at all says one began, so every fact it reports used to carry
no turn. The user's PROMPT is the boundary — a turn is the answer to something
somebody asked — so a prompt opens one and everything until the Stop rides it.

Kept per actor because a subagent's turns are its own, and per session because
one translator serves every session in the process.

Two deliberate limits. An ordinary prompt that arrives while a turn is open
does not open a second one: an injection is part of that turn. A queued prompt
with `promptSource=queued` is different. Claude can write it before the native
interrupt marker, although it starts the work after the interrupt. That prompt
replaces the open turn, and the later marker still belongs to the replaced turn.

A turn is named by its prompt id when the prompt record has one; see
`prompt_turns` for why.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain.ids import ActorId, SessionId, TurnId
from harness.impl.claude_code.canonical.prompt_turns import prompt_turn_id
from harness.impl.claude_code.ids import ClaudeCodePromptId, ClaudeCodeTurnId, turn_id_from_claude_code

if TYPE_CHECKING:
    from harness.impl.claude_code.ids import ClaudeCodeMessageId
    from harness.models.raw_events import (
        RawEvent,
    )


@dataclass(frozen=True)
class OpenTurn:
    """Represent open turn."""

    session_id: SessionId
    actor_id: ActorId
    turn_id: TurnId


def _event_actor_key(raw_event: RawEvent) -> tuple[SessionId, ActorId]:
    return raw_event.session_id, raw_event.actor_id


def _pop_finished_turn(
    finished_turns: list[OpenTurn],
    session_id: SessionId,
    actor_id: ActorId,
) -> TurnId | None:
    index = next(
        (
            index
            for index, finished in enumerate(finished_turns)
            if finished.session_id == session_id and finished.actor_id == actor_id
        ),
        None,
    )
    if index is not None:
        return finished_turns.pop(index).turn_id
    return None


class _TurnState:
    """Store state shared by turn operations."""

    def __init__(self) -> None:
        """Initialize the object."""
        self._open: list[OpenTurn] = []
        self._queued_interrupts: list[OpenTurn] = []
        self._transcript_finished: list[OpenTurn] = []
        self._hook_finished: list[OpenTurn] = []
        self._response_turns: dict[
            tuple[SessionId, ActorId, ClaudeCodeMessageId],
            TurnId,
        ] = {}
        self._started: set[OpenTurn] = set()

    def _has_open_turn(self, session_id: SessionId, actor_id: ActorId) -> bool:
        return any(
            opened.session_id == session_id and opened.actor_id == actor_id
            for opened in self._open
        )


class _OpenTurnLifecycle(_TurnState):
    """Open, inspect, and close turns."""

    def begin(self, raw_event: RawEvent, turn_id: TurnId) -> bool:
        """Open a turn, unless one already is. True when this prompt started it.

        Returns:
            True when the stated condition is met; otherwise, false.

        """
        session_id, actor_id = _event_actor_key(raw_event)
        if self._has_open_turn(session_id, actor_id):
            return False
        opened = OpenTurn(session_id, actor_id, turn_id)
        self._open.append(opened)
        self._started.add(opened)
        return True

    def prompt_turn(
        self, raw_event: RawEvent, prompt_id: ClaudeCodePromptId | None, record_identity: str,
    ) -> TurnId:
        """Name the turn that a transcript prompt record opens.

        A record without a prompt id keeps its record name. A second turn with
        the same prompt id, for example a subagent that its lead resumes in
        the same prompt, uses its record name too.

        Returns:
            The turn identifier.

        """
        record_turn = turn_id_from_claude_code(ClaudeCodeTurnId(record_identity))
        if prompt_id is None:
            return record_turn
        turn_id = prompt_turn_id(raw_event, prompt_id)
        if OpenTurn(raw_event.session_id, raw_event.actor_id, turn_id) in self._started:
            return record_turn
        return turn_id

    def current(self, raw_event: RawEvent) -> TurnId | None:
        """Return the current.

        Returns:
            Current.

        """
        session_id, actor_id = _event_actor_key(raw_event)
        return next(
            (
                opened.turn_id
                for opened in self._open
                if opened.session_id == session_id and opened.actor_id == actor_id
            ),
            None,
        )

    def close(self, raw_event: RawEvent) -> TurnId | None:
        """Close close.

        Returns:
            The turn id.

        """
        session_id, actor_id = _event_actor_key(raw_event)
        index = next(
            (
                index
                for index, opened in enumerate(self._open)
                if opened.session_id == session_id and opened.actor_id == actor_id
            ),
            None,
        )
        return None if index is None else self._open.pop(index).turn_id


class _TurnCompletion(_OpenTurnLifecycle):
    """Join transcript and hook turn completions."""

    def finished_by_transcript(
        self,
        raw_event: RawEvent,
        claude_code_message_id: ClaudeCodeMessageId,
        recovered_turn_id: TurnId | None,
    ) -> TurnId | None:
        """Correlate all copies of one response with one turn.

        Claude can write two cumulative transcript rows for one response. Its
        Stop hook can also arrive before either row. The stable response ID
        makes those copies one observation, and the hook-finished queue keeps a
        Stop that arrived first attached to the response that follows it.

        Returns:
            The turn id.

        """
        response_key = (*_event_actor_key(raw_event), claude_code_message_id)
        known = self._response_turns.get(response_key)
        if known is not None:
            return known

        session_id, actor_id = _event_actor_key(raw_event)
        resolved_turn_id = _pop_finished_turn(self._hook_finished, session_id, actor_id)
        if resolved_turn_id is None:
            resolved_turn_id = self.close(raw_event) or recovered_turn_id
            if resolved_turn_id is not None:
                self._transcript_finished.append(
                    OpenTurn(session_id, actor_id, resolved_turn_id),
                )
        if resolved_turn_id is not None:
            self._response_turns[response_key] = resolved_turn_id
        return resolved_turn_id

    def finished_by_hook(self, raw_event: RawEvent) -> TurnId | None:
        """Close a turn, or confirm a turn that its transcript closed first.

        Returns:
            The turn id.

        """
        session_id, actor_id = _event_actor_key(raw_event)
        turn_id = _pop_finished_turn(self._transcript_finished, session_id, actor_id)
        if turn_id is not None:
            return turn_id
        turn_id = self.close(raw_event)
        if turn_id is not None:
            self._hook_finished.append(
                OpenTurn(session_id, actor_id, turn_id),
            )
        return turn_id


class TurnSemantics(_TurnCompletion):
    """Track Claude Code turns by session and actor."""

    def replace_for_queued_prompt(self, raw_event: RawEvent) -> TurnId | None:
        """Close the old turn before Claude's queued prompt starts a new one.

        Returns:
            The turn id.

        """
        turn_id = self.close(raw_event)
        if turn_id is not None:
            self._queued_interrupts.append(
                OpenTurn(raw_event.session_id, raw_event.actor_id, turn_id),
            )
        return turn_id

    def interrupted(self, raw_event: RawEvent) -> tuple[TurnId | None, bool]:
        """Return the interrupted turn and whether its abort fact exists.

        Returns:
            Interrupted turn and whether its abort fact exists.

        """
        session_id, actor_id = _event_actor_key(raw_event)
        index = next(
            (
                index
                for index, opened in enumerate(self._queued_interrupts)
                if opened.session_id == session_id and opened.actor_id == actor_id
            ),
            None,
        )
        if index is not None:
            return self._queued_interrupts.pop(index).turn_id, True
        return self.close(raw_event), False

    def release_session(self, session_id: SessionId) -> None:
        """Release any turn that outlived a finished native session."""
        self._open = [opened for opened in self._open if opened.session_id != session_id]
        self._queued_interrupts = [opened for opened in self._queued_interrupts if opened.session_id != session_id]
        self._transcript_finished = [
            finished for finished in self._transcript_finished if finished.session_id != session_id
        ]
        self._hook_finished = [finished for finished in self._hook_finished if finished.session_id != session_id]
        self._started = {started for started in self._started if started.session_id != session_id}
        for key in tuple(self._response_turns):
            if key[0] == session_id:
                self._response_turns.pop(key)
