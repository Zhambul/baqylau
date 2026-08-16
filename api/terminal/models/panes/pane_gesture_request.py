# The base every pane gesture shares: what only the keypress process observes.
from pydantic import BaseModel

from api.common.models.fields import RequiredText


class PaneGestureRequest(BaseModel):
    working_directory: RequiredText
    window_id: str | None = None
