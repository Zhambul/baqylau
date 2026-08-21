# The background gesture: move the running command out of the foreground.
from api.controls.models.control_request import ControlRequestBody
from harness.models import Background
from domain.ids import RequestId, SessionId


class BackgroundRequest(ControlRequestBody):
    def request(self, session_id: SessionId) -> Background:
        return Background(session_id, RequestId(self.request_id))
