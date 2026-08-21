# The read-plan-choices gesture.
from api.common.models.fields import RequiredText
from api.controls.models.control_request import ControlRequestBody
from harness.models import ControlRequest, ReadPlanChoices
from domain.ids import AttentionId, RequestId, SessionId


class ReadPlanChoicesRequest(ControlRequestBody):
    attention_id: RequiredText

    def request(self, session_id: SessionId) -> ControlRequest:
        return ReadPlanChoices(
            session_id, RequestId(self.request_id), attention_id=AttentionId(self.attention_id)
        )
