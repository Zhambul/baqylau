# Which model a session, an actor or a change is talking about.
from pydantic import BaseModel


class ModelReferenceResponse(BaseModel):
    name: str
    display_name: str | None
