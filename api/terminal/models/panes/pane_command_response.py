# The gesture's verdict (handled=False: no session in this window).
from pydantic import BaseModel


class PaneCommandResponse(BaseModel):
    handled: bool
    succeeded: bool
    reason: str | None
