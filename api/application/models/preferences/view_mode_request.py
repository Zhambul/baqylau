# One session's chosen view mode.
from pydantic import BaseModel

from api.common.models.fields import RequiredText


class ViewModeRequest(BaseModel):
    view_mode: RequiredText
