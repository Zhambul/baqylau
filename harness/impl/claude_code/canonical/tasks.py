"""Claude Code task translation from task snapshots and lifecycle hooks."""

from __future__ import annotations

from domain.events import CanonicalEvent, EventPayload, TaskChanged
from domain.values import TaskState
from harness.impl.claude_code.canonical import records
from harness.impl.claude_code.canonical.support import event
from harness.impl.claude_code.ids import (
    ClaudeCodeActorId,
    ClaudeCodeTaskId,
    actor_id_from_claude_code,
    task_id_from_claude_code,
)
from harness.models import RawEvent, TranslationError


def _payload(
    raw_event: RawEvent,
    *,
    claude_code_task_id: ClaudeCodeTaskId | int | None,
    subject: str | None,
    description: str | None,
    task_state: TaskState,
    owner: str | None,
) -> TaskChanged:
    task_id = task_id_from_claude_code(ClaudeCodeTaskId(str(claude_code_task_id or "")))
    if not task_id:
        raise TranslationError("Claude Code task has no id", context=raw_event.source_position)
    owner_text = str(owner or "").strip()
    return TaskChanged(
        task_id,
        str(subject or ""),
        str(description or "").strip() or None,
        task_state,
        actor_id_from_claude_code(ClaudeCodeActorId(owner_text)) if owner_text else None,
    )


def task_file_event(
    raw_event: RawEvent,
    task: records.TaskFile,
) -> CanonicalEvent[EventPayload]:
    try:
        state = TaskState(task.status or "")
    except ValueError:
        raise TranslationError(
            f"unknown Claude Code task state: {task.status!r}",
            context=raw_event.source_position,
        ) from None
    payload = _payload(
        raw_event,
        claude_code_task_id=(
            ClaudeCodeTaskId(str(task.id)) if task.id is not None else None
        ),
        subject=task.subject,
        description=task.description,
        task_state=state,
        owner=task.owner,
    )
    # A task is mutable. Each complete file digest is a separate state fact.
    return event(
        raw_event,
        "task",
        str(payload.task_id),
        f"changed:{raw_event.source_position}",
        payload,
    )


def task_hook_event(
    raw_event: RawEvent,
    hook: records.HookPayload,
) -> CanonicalEvent[EventPayload]:
    hook_name = hook.hook_event_name or ""
    if hook_name == "TaskCreated":
        task_state = TaskState.PENDING
    elif hook_name == "TaskCompleted":
        task_state = TaskState.COMPLETED
    else:
        raise TranslationError(f"unknown Claude Code task hook: {hook_name!r}") from None
    payload = _payload(
        raw_event,
        claude_code_task_id=hook.task_id,
        subject=hook.task_subject,
        description=hook.task_description,
        task_state=task_state,
        owner=None,
    )
    return event(raw_event, "task", str(payload.task_id), f"changed:{hook_name}", payload)
