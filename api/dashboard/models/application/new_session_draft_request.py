# One per-directory new-session draft (sequence resolves write races).
from pydantic import BaseModel


class NewSessionDraftRequest(BaseModel):
    working_directory: str = ""
    text: str
    sequence: float
