# Toggle a terminal content view open/closed.
from pydantic import BaseModel

from api.common.models.fields import RequiredText


class ToggleViewRequest(BaseModel):
    content_reference: RequiredText
