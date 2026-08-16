# Whether the view ended up open.
from pydantic import BaseModel


class OpenedResponse(BaseModel):
    opened: bool
