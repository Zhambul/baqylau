# The remembered new-session form selections.
from pydantic import BaseModel


class NewSessionPreferencesRequest(BaseModel):
    working_directory: str | None = None
    harness: str | None = None
    model: str | None = None
    effort: str | None = None
