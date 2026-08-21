# The auto-name gesture.
from api.controls.models.control_request import ControlRequestBody
from harness.models import AutoNameSession
from domain.ids import RequestId, SessionId


class AutoNameSessionRequest(ControlRequestBody):
    def request(self, session_id: SessionId) -> AutoNameSession:
        return AutoNameSession(session_id, RequestId(self.request_id))
