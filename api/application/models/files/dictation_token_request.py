# The browser's dictation-grant request.
from pydantic import BaseModel

from api.common.models.fields import RequiredText


class DictationTokenRequest(BaseModel):
    sample_rate: int
    harness: RequiredText
    working_directory: str | None = None
