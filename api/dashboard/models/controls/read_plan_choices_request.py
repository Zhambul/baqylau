# The read-plan-choices gesture.
from api.common.models.fields import RequiredText
from api.dashboard.models.controls.control_request import ControlRequestBody
from contracts.harness import ControlRequest, ReadPlanChoices
from domain.ids import AttentionId, SessionId


class ReadPlanChoicesRequest(ControlRequestBody):
    attention_id: RequiredText

    def request(self, session_id: SessionId) -> ControlRequest:
        return ReadPlanChoices(
            session_id, self.request_id, attention_id=AttentionId(self.attention_id)
        )
