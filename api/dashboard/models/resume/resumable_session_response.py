# One session the picker can resume into.
from pydantic import BaseModel

from domain.ids import SessionId

from api.common.models.values.account_reference import AccountReferenceResponse
from api.common.models.values.model_reference import ModelReferenceResponse


class ResumableSessionResponse(BaseModel):
    session_id: SessionId
    title: str | None
    last_activity_at: float
    active: bool
    harness: str
    model: ModelReferenceResponse | None
    effort: str | None
    account: AccountReferenceResponse | None
