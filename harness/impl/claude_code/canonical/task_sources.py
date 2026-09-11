# Copyright (c) 2026 Zhambyl Yermagambet
"""Read Claude Code task files as raw events."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import TYPE_CHECKING

from domain import ids as domain_ids
from harness import contract as harness_contract
from harness.impl.claude_code import ids as claude_ids
from harness.impl.claude_code.canonical import records
from harness.models import raw_events as raw_event_models

if TYPE_CHECKING:
    from harness.models import session as session_models

HARNESS = domain_ids.HarnessName("claude_code")
TEXT_ENCODING = "utf-8"


class ClaudeTaskRawEventSource(harness_contract.HarnessRawEventSource):
    """Capture Claude Code task files as immutable raw observations."""

    def __init__(self, session: session_models.Session, configuration_directory: str) -> None:
        """Initialize the task source."""
        self.session = session
        native_session_id = claude_ids.claude_code_session_id_from_domain(session.session_id)
        session_prefix = str(native_session_id).split("-", 1)[0]
        self.task_directory = Path(configuration_directory) / "tasks" / f"session-{session_prefix}"
        self.source_identity = f"claude_code:tasks:{session.session_id}"

    def read(self, after_position: str | None) -> tuple[raw_event_models.RawEvent, ...]:
        """Return task-file events after one position.

        Returns:
            The task raw events.

        """
        current = self._current_tasks()
        if not current and after_position is None:
            return ()
        position, snapshot_digest = _task_position(current)
        if position == after_position:
            return ()
        raw_events = [self._task_event(task) for task in current]
        raw_events.append(self._membership_event(current, after_position, position, snapshot_digest))
        return tuple(raw_events)

    def _current_tasks(self) -> list[records.TaskFile]:
        current: list[records.TaskFile] = []
        for path in sorted(self.task_directory.glob("*.json")):
            try:
                with path.open(encoding=TEXT_ENCODING) as source:
                    task = records.TaskFile.model_validate_json(source.read())
            except (OSError, UnicodeDecodeError):
                continue
            if task.id is not None:
                current.append(task)
        return current

    def _task_event(self, task: records.TaskFile) -> raw_event_models.RawEvent:
        encoded = task.model_dump_json(exclude_none=True)
        digest = hashlib.sha256(encoded.encode(TEXT_ENCODING)).hexdigest()
        return raw_event_models.RawEvent(
            raw_event_id=domain_ids.RawEventId(f"{self.source_identity}:{task.id}:{digest}"),
            harness=HARNESS,
            source_type="tasks",
            source_name=str(self.task_directory),
            source_position=f"{task.id}:{digest}",
            session_id=self.session.session_id,
            actor_id=self.session.lead_actor_id,
            parent_actor_id=None,
            observed_at=time.time(),
            encoding="json",
            payload=encoded.encode(TEXT_ENCODING),
            source_identity=self.source_identity,
        )

    def _membership_event(
        self,
        current: list[records.TaskFile],
        after_position: str | None,
        position: str,
        snapshot_digest: str,
    ) -> raw_event_models.RawEvent:
        membership = records.TaskListDocument(
            list_id=claude_ids.ClaudeCodeTaskListId("session"),
            task_ids=[str(task.id) for task in current],
        ).model_dump_json(exclude_none=True)
        previous_position = after_position or ""
        revision = hashlib.sha256(f"{previous_position}::{snapshot_digest}".encode()).hexdigest()
        return raw_event_models.RawEvent(
            raw_event_id=domain_ids.RawEventId(f"{self.source_identity}:list:{revision}"),
            harness=HARNESS,
            source_type="task_list",
            source_name=str(self.task_directory),
            source_position=position,
            session_id=self.session.session_id,
            actor_id=self.session.lead_actor_id,
            parent_actor_id=None,
            observed_at=time.time(),
            encoding="json",
            payload=membership.encode(TEXT_ENCODING),
            source_identity=self.source_identity,
        )


def _task_position(current: list[records.TaskFile]) -> tuple[str, str]:
    snapshot = records.TaskSnapshot(tuple(current)).model_dump_json(exclude_none=True)
    snapshot_digest = hashlib.sha256(snapshot.encode(TEXT_ENCODING)).hexdigest()
    return f"list:{snapshot_digest}", snapshot_digest
