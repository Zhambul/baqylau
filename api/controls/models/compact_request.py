# The compact gesture.
from api.controls.models.control_request import ControlRequestBody
from harness.models import Compact, ControlRequest
from domain.ids import SessionId


class CompactRequest(ControlRequestBody):
    def request(self, session_id: SessionId) -> ControlRequest:
        return Compact(session_id, self.request_id)
