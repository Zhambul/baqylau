# The close-session gesture.
from api.controls.models.control_request import ControlRequestBody
from harness.models import CloseSession
from domain.ids import RequestId, SessionId


class CloseSessionRequest(ControlRequestBody):
    def request(self, session_id: SessionId) -> CloseSession:
        return CloseSession(session_id, RequestId(self.request_id))
