# The open-rewind gesture.
from api.dashboard.models.controls.control_request import ControlRequestBody
from contracts.harness import ControlRequest, OpenRewind
from domain.ids import SessionId


class OpenRewindRequest(ControlRequestBody):
    def request(self, session_id: SessionId) -> ControlRequest:
        return OpenRewind(session_id, self.request_id)
