# The compact gesture.
from api.controls.models.control_request import ControlRequestBody
from harness.models import Compact, ControlRequest
from domain.ids import RequestId, SessionId


class CompactRequest(ControlRequestBody):
    def request(self, session_id: SessionId) -> ControlRequest:
        return Compact(session_id, RequestId(self.request_id))
