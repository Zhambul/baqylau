# The interrupt gesture.
from api.controls.models.control_request import ControlRequestBody
from harness.models import Interrupt
from domain.ids import RequestId, SessionId


class InterruptRequest(ControlRequestBody):
    def request(self, session_id: SessionId) -> Interrupt:
        return Interrupt(session_id, RequestId(self.request_id))
