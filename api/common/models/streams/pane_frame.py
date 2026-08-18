# The two frames a terminal pane's stream sends: which session it bound to, and
# one painted screen.
from pydantic import BaseModel


class PaneSessionFrame(BaseModel):
    session_id: str


class PaneScreenFrame(BaseModel):
    ansi: str
