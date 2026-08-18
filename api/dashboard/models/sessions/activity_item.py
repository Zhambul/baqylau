# One rendered row of the feed: what it is, how far along it is, the escaped
# HTML the page draws, and the plain text behind it. Every optional field below
# belongs to one KIND of item — a file's line counts, an assignment's phase, an
# operation's two content references — and is absent-as-null on the rest.
from typing import Literal

from pydantic import BaseModel

from domain.ids import ActorId, AssignmentId, MessageId, TurnId


class ActivityItemResponse(BaseModel):
    item_id: str
    item_type: Literal[
        "message",
        "reasoning",
        "operation",
        "file",
        "attention",
        "task",
        "compaction",
        "actor_assignment",
        "actor_message",
        "model_changed",
        "effort_changed",
    ]
    summary_kind: Literal[
        "message",
        "shell",
        "background",
        "monitor",
        "file_read",
        "file_write",
        "file_edit",
        "search",
        "network",
        "workspace",
        "media",
        "skill",
        "tool",
        "task",
        "message_delivery",
        "attention",
        "compaction",
        "actor_assignment",
        "actor_message",
        "model_changed",
        "effort_changed",
    ]
    actor_id: ActorId
    state: Literal["running", "succeeded", "failed", "cancelled"] | None
    html: str
    plain_text: str
    content_reference: str | None
    conversation_kind: Literal[
        "prompt",
        "message",
        "system",
        "question",
        "answer",
        "plan",
        "plan_decision",
        "recap",
        "actor_message",
    ] | None
    turn_id: TurnId | None
    final: bool
    actor_assignment_id: AssignmentId | None
    actor_assignment_phase: Literal["started", "finished"] | None
    lines_added: int | None
    lines_removed: int | None
    message_id: MessageId | None
    reply_to_message_id: MessageId | None
    started_at: float | None
    finished_at: float | None
    command_reference: str | None
    output_reference: str | None
    file_path: str | None
