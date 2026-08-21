# The background gesture: move the running command out of the foreground.
from api.controls.models.control_request import ControlRequestBody
from harness.models import Background, ControlRequest
from domain.ids import RequestId, SessionId


class BackgroundRequest(ControlRequestBody):
    def request(self, session_id: SessionId) -> ControlRequest:
        return Background(session_id, RequestId(self.request_id))
