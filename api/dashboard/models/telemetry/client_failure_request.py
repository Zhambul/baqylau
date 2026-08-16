# One failed browser gesture report.
from typing import Literal

from pydantic import BaseModel

from api.common.models.fields import RequiredText


class ClientFailureRequest(BaseModel):
    gesture: RequiredText
    failure_kind: Literal["transport", "http"]
    error: str | None = None
    status_code: int | None = None
    character_count: int | None = None
