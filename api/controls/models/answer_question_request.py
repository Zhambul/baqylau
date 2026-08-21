# The answer-question gesture: one selection per question the session asked,
# aligned with the prompts it asked them in.
#
# `answers` used to be a bare `Any` re-encoded with json.dumps — the browser
# could post anything at all and the first thing that looked at it was a
# terminal driver two layers down, which read it as
# `[{"selected": [...], "other": "..."}]` and had no way to say so. It is the
# same shape the dialog DRAFT already declared (AnswerSelectionBody), and it is
# declared here now: a malformed answer is a 400 at the boundary.
from typing import Literal

from pydantic import RootModel

from api.common.models.fields import RequiredText
from api.application.models.preferences.dialog_draft_request import AnswerSelectionBody
from api.controls.models.control_request import ControlRequestBody
from harness.models import AnswerQuestion, ControlRequest
from domain.ids import AttentionId, RequestId, SessionId
from domain.values import StructuredContent


class AnswerDocument(RootModel[tuple[AnswerSelectionBody, ...]]):
    """The answers as the harness layer carries them — a StructuredContent, so
    a document. The model does the encoding; nothing here calls json.dumps."""


class AnswerQuestionRequest(ControlRequestBody):
    attention_id: RequiredText
    decision: Literal["answer", "discuss"]
    answers: tuple[AnswerSelectionBody, ...] | None = None
    discussion: str | None = None

    def request(self, session_id: SessionId) -> ControlRequest:
        return AnswerQuestion(
            session_id,
            RequestId(self.request_id),
            attention_id=AttentionId(self.attention_id),
            decision=self.decision,
            answers=(
                StructuredContent(AnswerDocument(self.answers).model_dump_json())
                if self.answers is not None
                else None
            ),
            discussion=self.discussion,
        )
