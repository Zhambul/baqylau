# The auto-name gesture.
from api.controls.models.control_request import ControlRequestBody
from harness.models import AutoNameSession, ControlRequest
from domain.ids import SessionId


class AutoNameSessionRequest(ControlRequestBody):
    def request(self, session_id: SessionId) -> ControlRequest:
        return AutoNameSession(session_id, self.request_id)
