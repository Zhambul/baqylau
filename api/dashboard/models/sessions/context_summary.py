# How full each actor's context window is, and who is compacting right now.
from pydantic import BaseModel

from domain.ids import ActorId

from api.common.models.values.model_reference import ModelReferenceResponse

class ContextWindowResponse(BaseModel):
    used_tokens: int
    window_tokens: int
    model: ModelReferenceResponse | None


class ContextSummaryResponse(BaseModel):
    by_actor: dict[ActorId, ContextWindowResponse]
    compacting_actor_ids: tuple[ActorId, ...]
