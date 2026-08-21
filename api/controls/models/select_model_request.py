# The model-selection gesture.
from api.common.models.fields import RequiredText
from api.controls.models.control_request import ControlRequestBody
from harness.models import SelectModel
from domain.ids import ModelId, RequestId, SessionId


class SelectModelRequest(ControlRequestBody):
    model_id: RequiredText

    def request(self, session_id: SessionId) -> SelectModel:
        return SelectModel(session_id, RequestId(self.request_id), model_id=ModelId(self.model_id))
