# One failed browser gesture report.
from enum import StrEnum

from pydantic import BaseModel

from api.common.models.fields import RequiredText


class ClientFailureKind(StrEnum):
    TRANSPORT = "transport"
    HTTP = "http"


class ClientFailureRequest(BaseModel):
    gesture: RequiredText
    failure_kind: ClientFailureKind
    error: str | None = None
    status_code: int | None = None
    character_count: int | None = None
