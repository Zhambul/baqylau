# What a session IS, folded from canonical facts — the same for every reader.
from typing import Literal

from pydantic import BaseModel

from domain.ids import ActorId, SessionId

from api.common.models.values.account_reference import AccountReferenceResponse
from api.common.models.values.model_reference import ModelReferenceResponse
from api.dashboard.models.sessions.model_change import ModelChangeResponse

class SessionSummaryResponse(BaseModel):
    session_id: SessionId
    harness: str
    title: str | None
    working_directory: str
    initial_working_directory: str
    started_at: float
    finished_at: float | None
    lead_actor_id: ActorId
    model: ModelReferenceResponse | None
    effort: str | None
    account: AccountReferenceResponse | None
    prompt_count: int
    automatic_model_change: ModelChangeResponse | None
    state: Literal["running", "finished"]
