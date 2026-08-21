# The composer's queued messages.
from pydantic import BaseModel


class QueuedMessageBody(BaseModel):
    text: str


class ComposerQueueRequest(BaseModel):
    items: tuple[QueuedMessageBody, ...]
    origin: str
