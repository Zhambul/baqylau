# The first frame of the global stream: which boot of the daemon this
# connection is talking to. A changed boot id is the page's signal to reload.
from pydantic import BaseModel


class ReadyFrame(BaseModel):
    boot_id: str
