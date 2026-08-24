"""The server identity sent when the global event stream opens."""
from pydantic import BaseModel


class ReadyFrame(BaseModel):
    boot_id: str
