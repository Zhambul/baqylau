# Copyright (c) 2026 Zhambyl Yermagambet
"""Check the Claude record fields that a new harness version added.

Every model here FORBIDS an unknown field, and each refused record takes
something down: a sidecar stops every source of its session, an attachment stops
the transcript, and a notification leaves the lead waiting for a subagent that
already finished. A field is listed as soon as a record carries it.
"""

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
