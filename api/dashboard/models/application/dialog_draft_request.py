# The unsubmitted answers of one attention dialog.
from pydantic import BaseModel

from api.common.models.fields import RequiredText


class AnswerSelectionBody(BaseModel):
    selected: tuple[str, ...]
    other: str


class DialogDraftRequest(BaseModel):
    attention_id: RequiredText
    origin: str
    answers: tuple[AnswerSelectionBody, ...]
