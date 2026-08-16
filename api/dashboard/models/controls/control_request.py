# The base every control-gesture request shares: the caller's request id.
from pydantic import BaseModel

from api.common.models.fields import RequiredText


class ControlRequestBody(BaseModel):
    request_id: RequiredText
