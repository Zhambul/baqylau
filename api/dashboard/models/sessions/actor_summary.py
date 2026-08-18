# One worker in a session: the lead, or a subagent under it.
from typing import Literal

from pydantic import BaseModel

from domain.ids import ActorId

from api.common.models.values.model_reference import ModelReferenceResponse

class ActorSummaryResponse(BaseModel):
    actor_id: ActorId
    parent_actor_id: ActorId | None
    harness: str
    role: Literal["lead", "child", "teammate", "sidecar"]
    name: str
    description: str | None
    model: ModelReferenceResponse | None
    effort: str | None
    state: Literal["running", "finished"]
    started_at: float | None
    finished_at: float | None
