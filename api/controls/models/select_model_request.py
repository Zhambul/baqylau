# The model-selection gesture.
from api.common.models.fields import RequiredText
from api.controls.models.control_request import ControlRequestBody
from harness.models import ControlRequest, SelectModel
from domain.ids import SessionId


class SelectModelRequest(ControlRequestBody):
    model_id: RequiredText

    def request(self, session_id: SessionId) -> ControlRequest:
        return SelectModel(session_id, self.request_id, model_id=self.model_id)
