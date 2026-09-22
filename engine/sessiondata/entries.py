# Copyright (c) 2026 Zhambyl Yermagambet
"""Canonical event → feed entry, one mapping per feed-worthy kind.

The one place the two vocabularies meet. Everything a reader is shown is decided
here, once, at push time: which facts appear at all, and what each carries after
the audit detail is dropped.

Events that produce nothing are as deliberate as the ones that do —
`session.*`, `actor.*`, `task.*`, `goal.changed`, `usage.reported`,
`context.reported`, `shell.input_provided` and `shell.output_located` feed the
aggregate or the daemon's own machinery, and a feed that showed them would be
showing plumbing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain import (
    entries,
    entry_base,
    entry_conversation,
    entry_shells,
    event_conversation,
    event_shell,
    outcomes,
)
from engine.sessiondata import (
    contract,
    entry_resource_bodies,
    entry_selection_bodies,
    entry_summaries,
    entry_work_bodies,
    naming,
)

if TYPE_CHECKING:
    from domain import event_base, ids


def run_state(outcome: outcomes.Outcome) -> entry_base.RunState:
    """Run state.

    A feed shows three ends, not five. `rejected` is a refusal to run, which
        is a failure to whoever was waiting for it, and `unknown` is the honest
        answer to "did it work?" only where somebody can act on it — nobody can.

    Returns:
        The run state.

    """
    if outcome == outcomes.Outcome.CANCELLED:
        return entry_base.RunState.CANCELLED
    return entry_base.RunState.SUCCEEDED if outcome == outcomes.Outcome.SUCCEEDED else entry_base.RunState.FAILED


def file_state(outcome: outcomes.Outcome) -> entry_base.FileState:
    """Return the file state.

    Returns:
        File state.

    """
    return entry_base.FileState.SUCCEEDED if outcome == outcomes.Outcome.SUCCEEDED else entry_base.FileState.FAILED


class EntryWriter(contract.SessionEntryWriter):
    """Represent entry writer."""

    def __init__(self, model_naming: naming.ModelNaming | None = None) -> None:
        """Initialize the object."""
        self.model_naming = model_naming or naming.ModelNaming()

    def entries(
        self, canonical_event: event_base.CanonicalEvent[event_base.EventPayload],
    ) -> tuple[entries.SessionEntry, ...]:
        """Return the entries.

        Returns:
            The ordered entries.

        """
        event = canonical_event
        body = _body(event.payload, event.harness, self.model_naming)
        if body is None:
            return ()
        return (entries.SessionEntry(
            entry_id=event.event_id,
            session_id=event.session_id,
            actor_id=event.actor_id,
            parent_actor_id=event.parent_actor_id,
            turn_id=event.turn_id,
            # Always a number: a feed shows when things happened, and a source
            # that carries no clock of its own would otherwise leave a hole in
            # the middle of a conversation.
            occurred_at=canonical_event.happened_at,
            summary=entry_summaries.summary(event.payload),
            body=body,
        ),)


def _body(
    event_payload: event_base.EventPayload,
    harness: ids.HarnessName,
    model_naming: naming.ModelNaming,
) -> entry_base.EntryBody | None:
    for mapped in (
        _conversation_body(event_payload),
        _shell_body(event_payload),
        entry_resource_bodies.resource_body(event_payload),
        entry_work_bodies.work_body(event_payload),
    ):
        if mapped is not None:
            return mapped
    return entry_selection_bodies.selection_body(event_payload, harness, model_naming)


def _conversation_body(event_payload: event_base.EventPayload) -> entry_base.EntryBody | None:
    if isinstance(event_payload, event_conversation.TurnStarted):
        return entry_conversation.TurnStartedBody(event_payload.prompt_message_id)
    if isinstance(event_payload, event_conversation.TurnFinished):
        return entry_conversation.TurnFinishedBody(entry_base.TurnState.FINISHED)
    if isinstance(event_payload, event_conversation.TurnAborted):
        return entry_conversation.TurnFinishedBody(entry_base.TurnState.ABORTED)
    if isinstance(event_payload, event_conversation.MessageCreated):
        return entry_conversation.MessageBody(
            event_payload.message_id,
            event_payload.role,
            event_payload.phase,
            event_payload.content,
            event_payload.recipient_actor_id,
            event_payload.reply_to,
        )
    if isinstance(event_payload, event_conversation.ReasoningCreated):
        body: entry_base.EntryBody | None = entry_conversation.ReasoningBody(
            event_payload.reasoning_id,
            event_payload.content,
        )
    else:
        body = None
    return body


def _shell_body(event_payload: event_base.EventPayload) -> entry_base.EntryBody | None:
    if isinstance(event_payload, event_shell.ShellStarted):
        return entry_shells.ShellStartedBody(event_payload.shell_id, event_payload.command, event_payload.execution)
    if isinstance(event_payload, event_shell.ShellProgressed):
        return entry_shells.ShellOutputBody(
            event_payload.shell_id,
            event_payload.stream,
            event_payload.mode,
            event_payload.content,
        )
    if isinstance(event_payload, event_shell.ShellBackgrounded):
        return entry_shells.ShellBackgroundedBody(event_payload.shell_id)
    if isinstance(event_payload, event_shell.ShellFinished):
        return entry_shells.ShellFinishedBody(
            event_payload.shell_id,
            run_state(event_payload.outcome),
            event_payload.exit_code,
            event_payload.result,
        )
    return None
