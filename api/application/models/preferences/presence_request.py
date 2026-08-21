# One device's presence beat.
from pydantic import BaseModel

from api.common.models.fields import RequiredText


class PresenceRequest(BaseModel):
    device_id: RequiredText
    session_id: str | None = None
    away: bool = False
