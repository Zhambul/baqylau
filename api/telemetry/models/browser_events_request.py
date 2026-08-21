# One batch of frontend-audit events (scalar details only).
from pydantic import BaseModel

from api.common.models.fields import RequiredText, Scalar


class BrowserEventBody(BaseModel):
    name: RequiredText
    session_id: str | None = None
    timestamp: int | None = None
    details: dict[str, Scalar] = {}


class BrowserEventsRequest(BaseModel):
    client_id: RequiredText
    device_id: RequiredText
    connection: dict[str, Scalar] = {}
    events: tuple[BrowserEventBody, ...]
