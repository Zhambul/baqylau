# One entry of the session's task list.
from typing import Literal

from pydantic import BaseModel

from domain.ids import ActorId, TaskId


class TaskSummaryResponse(BaseModel):
    task_id: TaskId
    label: str
    subject: str
    description: str | None
    state: Literal["pending", "in_progress", "completed"]
    owner_actor_id: ActorId | None
