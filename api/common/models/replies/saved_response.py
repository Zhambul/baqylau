# The persisted-preference reply.
from pydantic import BaseModel


class SavedResponse(BaseModel):
    saved: bool = True
