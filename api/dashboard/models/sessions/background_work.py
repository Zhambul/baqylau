# Work that outlives the turn that started it: backgrounded commands, and
# monitors armed to report until they are stopped. Two lists off one snapshot,
# because they are two different facts.
from pydantic import BaseModel

from domain.ids import ActorId, OperationId


class MonitorEventResponse(BaseModel):
    event: str
    status: str | None
    summary: str | None
    timestamp: float | None


class BackgroundOperationResponse(BaseModel):
    task: str
    actor_id: ActorId
    command: str
    command_html: str
    description: str | None
    live: bool
    started_at: float | None
    ended_at: float | None
    end_reason: str | None
    output: str
    line_count: int
    events: tuple[MonitorEventResponse, ...]


class BackgroundWorkResponse(BaseModel):
    running_operation_ids: tuple[OperationId, ...]
    monitor_count: int
    background_job_count: int
    monitors: tuple[BackgroundOperationResponse, ...]
    jobs: tuple[BackgroundOperationResponse, ...]
