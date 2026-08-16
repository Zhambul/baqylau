# One session's notification mute switch.
from pydantic import BaseModel


class NotificationsMutedRequest(BaseModel):
    muted: bool
