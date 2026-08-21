# The open-rewind gesture.
from api.controls.models.control_request import ControlRequestBody
from harness.models import ControlRequest, OpenRewind
from domain.ids import SessionId


class OpenRewindRequest(ControlRequestBody):
    def request(self, session_id: SessionId) -> ControlRequest:
        return OpenRewind(session_id, self.request_id)
