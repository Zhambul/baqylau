# Basenames of files pasted as zero-byte promises.
from typing import Annotated

from pydantic import BaseModel, Field


class ClipboardFilesRequest(BaseModel):
    names: Annotated[tuple[str, ...], Field(min_length=1)]
    session_id: str | None = None
