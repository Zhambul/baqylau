# The auto-name gesture.
from api.dashboard.models.controls.control_request import ControlRequestBody
from contracts.harness import ControlRequest, AutoNameSession
from domain.ids import SessionId


class AutoNameSessionRequest(ControlRequestBody):
    def request(self, session_id: SessionId) -> ControlRequest:
        return AutoNameSession(session_id, self.request_id)
