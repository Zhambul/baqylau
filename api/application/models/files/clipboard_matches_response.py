# The host paths whose basenames matched exactly (empty on a miss).
from pydantic import BaseModel


class ClipboardMatchesResponse(BaseModel):
    paths: tuple[str, ...]
