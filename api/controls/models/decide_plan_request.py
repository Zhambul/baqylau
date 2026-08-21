# The plan-decision gesture.
from api.common.models.fields import RequiredText
from api.controls.models.control_request import ControlRequestBody
from harness.models import ControlRequest, DecidePlan
from domain.ids import AttentionId, RequestId, SessionId


class DecidePlanRequest(ControlRequestBody):
    attention_id: RequiredText
    decision: RequiredText
    feedback: str | None = None

    def request(self, session_id: SessionId) -> ControlRequest:
        return DecidePlan(
            session_id,
            RequestId(self.request_id),
            attention_id=AttentionId(self.attention_id),
            decision=self.decision,
            feedback=self.feedback,
        )
