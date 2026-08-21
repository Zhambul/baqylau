# The Web Push feature probe's answer.
from pydantic import BaseModel


class PushConfigurationResponse(BaseModel):
    enabled: bool
    key: str | None
