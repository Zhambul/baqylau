# The last frame of a stream that failed. The connection ends after it, so the
# client reconnects; what actually happened is in the audit row.
from pydantic import BaseModel


class ErrorFrame(BaseModel):
    error: str
