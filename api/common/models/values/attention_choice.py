# One selectable answer to a question the session asked.
from pydantic import BaseModel


class AttentionChoiceResponse(BaseModel):
    value: str
    label: str
    description: str | None
