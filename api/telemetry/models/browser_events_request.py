from collections.abc import Mapping

from pydantic import BaseModel

from api.common.models.fields import RequiredText, Scalar


class BrowserEventBody(BaseModel):
    name: RequiredText
    session_id: str | None = None
    timestamp: int | None = None
    details: Mapping[str, Scalar] = {}


class BrowserEventsRequest(BaseModel):
    client_id: RequiredText
    device_id: RequiredText
    connection: Mapping[str, Scalar] = {}
    events: tuple[BrowserEventBody, ...]
