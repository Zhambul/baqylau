# The short-lived grant plus the assembled listen URL.
from pydantic import BaseModel


class DictationGrantResponse(BaseModel):
    token: str
    expires_in: int | None
    ws_url: str
