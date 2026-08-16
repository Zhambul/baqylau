# The answer-question gesture (answers as arbitrary JSON).
import json
from typing import Any, Literal

from api.common.models.fields import RequiredText
from api.dashboard.models.controls.control_request import ControlRequestBody
from harness.models import AnswerQuestion, ControlRequest
from domain.ids import AttentionId, SessionId
from domain.values import StructuredContent


class AnswerQuestionRequest(ControlRequestBody):
    attention_id: RequiredText
    decision: Literal["answer", "discuss"]
    answers: Any = None
    discussion: str | None = None

    def request(self, session_id: SessionId) -> ControlRequest:
        return AnswerQuestion(
            session_id,
            self.request_id,
            attention_id=AttentionId(self.attention_id),
            decision=self.decision,
            answers=(
                StructuredContent(json.dumps(self.answers, ensure_ascii=False))
                if self.answers is not None
                else None
            ),
            discussion=self.discussion,
        )
