# The compact gesture.
from api.dashboard.models.controls.control_request import ControlRequestBody
from contracts.harness import ControlRequest, Compact
from domain.ids import SessionId


class CompactRequest(ControlRequestBody):
    def request(self, session_id: SessionId) -> ControlRequest:
        return Compact(session_id, self.request_id)
