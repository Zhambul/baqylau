# Which model a session, an actor or a change is talking about. `native_id` is
# what the harness reported; `selection_id` is what the picker would send back.
from pydantic import BaseModel


class ModelReferenceResponse(BaseModel):
    native_id: str
    display_name: str | None
    selection_id: str | None
