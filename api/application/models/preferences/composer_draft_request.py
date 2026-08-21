# The composer's unsent text (sequence resolves write races).
from pydantic import BaseModel


class ComposerDraftRequest(BaseModel):
    text: str
    origin: str
    sequence: float
