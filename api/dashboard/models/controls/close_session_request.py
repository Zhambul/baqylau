# The close-session gesture.
from api.dashboard.models.controls.control_request import ControlRequestBody
from contracts.harness import ControlRequest, CloseSession
from domain.ids import SessionId


class CloseSessionRequest(ControlRequestBody):
    def request(self, session_id: SessionId) -> ControlRequest:
        return CloseSession(session_id, self.request_id)
