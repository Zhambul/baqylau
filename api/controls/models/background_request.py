# The background gesture: move the running command out of the foreground.
from api.controls.models.control_request import ControlRequestBody
from harness.models import Background, ControlRequest
from domain.ids import SessionId


class BackgroundRequest(ControlRequestBody):
    def request(self, session_id: SessionId) -> ControlRequest:
        return Background(session_id, self.request_id)
