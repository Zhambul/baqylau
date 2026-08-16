# The plan-decision gesture.
from api.common.models.fields import RequiredText
from api.dashboard.models.controls.control_request import ControlRequestBody
from contracts.harness import ControlRequest, DecidePlan
from domain.ids import AttentionId, SessionId


class DecidePlanRequest(ControlRequestBody):
    attention_id: RequiredText
    decision: RequiredText
    feedback: str | None = None

    def request(self, session_id: SessionId) -> ControlRequest:
        return DecidePlan(
            session_id,
            self.request_id,
            attention_id=AttentionId(self.attention_id),
            decision=self.decision,
            feedback=self.feedback,
        )
