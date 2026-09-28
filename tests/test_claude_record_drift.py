# Copyright (c) 2026 Zhambyl Yermagambet
"""Check the Claude record fields that a new harness version added.

Every model here FORBIDS an unknown field, and each refused record takes
something down: a sidecar stops every source of its session, an attachment stops
the transcript, and a notification leaves the lead waiting for a subagent that
already finished. A field is listed as soon as a record carries it.
"""

import pytest

from harness.impl.claude_code import model, model_names
from harness.impl.claude_code.canonical import record_attachments, record_documents, record_transcript_entries


def test_background_subagent_sidecar_fields() -> None:
    """Keep reading a subagent sidecar that names its request shape.

    Claude Code 2.1.269 added `requestShape`, `requestNonInteractive` and
    `spawnedWithWorktree`. The sidecar model forbids an unknown field, and a
    sidecar it refuses takes down every source of its session, so the subagent
    never becomes an actor.
    """
    record = record_documents.AgentMetaFile.model_validate({
        "agentType": "general-purpose",
        "description": "e2e_ticker_work",
        "toolUseId": "toolu_01HUqJNxyRW7oeSxWd63XR3t",
        "spawnDepth": 1,
        "requestShape": "background",
        "requestNonInteractive": True,
        "spawnedWithWorktree": True,
    })

    assert record.tool_use_id == "toolu_01HUqJNxyRW7oeSxWd63XR3t"
    assert record.request_shape == "background"
    assert record.request_non_interactive is True
    assert record.spawned_with_worktree is True


def test_queued_command_attachment_rendered_twice() -> None:
    """Keep reading an attachment that Claude renders in the human turn.

    Claude Code 2.1.269 added `renderedInHumanTurn`. The attachment model
    forbids an unknown field, and a record it refuses stops the transcript of
    its session.
    """
    record = record_attachments.AttachmentRecord[record_attachments.AttachmentHeader].model_validate({
        "type": "attachment",
        "attachment": {"type": "queued_command", "prompt": "<task-notification/>"},
        "rendered": [{"content": "<system-reminder>one</system-reminder>"}],
        "renderedInHumanTurn": [{"content": "<system-reminder>one</system-reminder>"}],
    })

    assert record.attachment is not None
    assert record.attachment.type == "queued_command"
    assert [block.content for block in record.rendered_in_human_turn or ()] == [
        "<system-reminder>one</system-reminder>",
    ]


def test_teammate_idle_notification_result() -> None:
    """Keep reading an idle notification that carries the answer of a teammate.

    Claude Code 2.1.269 added `result`. The notification model forbids an
    unknown field, and the lead waits for a subagent that already finished when
    the record is refused.
    """
    record = record_transcript_entries.TeammateIdleNotificationDocument.model_validate({
        "type": "idle_notification",
        "from": "e2e_terminal_color_work",
        "idleReason": "completed",
        "result": "TERMINAL_COLOR_DONE",
    })

    assert record.idle_reason == "completed"
    assert record.result == "TERMINAL_COLOR_DONE"


def test_queued_goal_check_in_user_record() -> None:
    """Keep reading a user record that Claude Code queued for a goal check-in.

    Claude Code 2.1.x added `queuePriority` and `queueOrigin`. The user record
    model forbids an unknown field, and a record it refuses is lost.
    """
    record = record_transcript_entries.UserRecord.model_validate({
        "message": {"role": "user", "content": "<task-notification>check</task-notification>"},
        "queuePriority": "later",
        "queueOrigin": {"kind": "task-notification", "source": "goal-checkin"},
    })

    assert record.queue_priority == "later"
    assert record.queue_origin is not None
    assert record.queue_origin.source == "goal-checkin"


NEW_MODELS = ("claude-opus-5-5", "claude-opus-5-5[1m]", "claude-sonnet-6", "opusplan", "opus[1m]")


@pytest.mark.parametrize("model_name", NEW_MODELS)
def test_a_new_model_id_is_read(model_name: str) -> None:
    """Read a model ID that is not in the known list, and keep its `[1m]` window.

    A model ID in a closed list failed the translation of every assistant record
    of the session at each model release. A record's model is checked by its shape.
    """
    assert model_names.record_model(model_name) == model_name


def test_a_million_suffix_keeps_the_large_window() -> None:
    """The `[1m]` form of a model that is not in the known list still has the large window."""
    assert model.window("claude-opus-4-1[1m]") == model.LARGE_CONTEXT_WINDOW


@pytest.mark.parametrize("model_name", ["", "gpt-5", "claude-", "Claude Opus", "opus[2m]"])
def test_a_malformed_model_name_is_refused(model_name: str) -> None:
    """A value that has no model shape still fails its own record."""
    with pytest.raises(ValueError, match="not a Claude Code model name"):
        model_names.record_model(model_name)
