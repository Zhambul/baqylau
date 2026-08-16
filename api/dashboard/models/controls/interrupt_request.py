# The interrupt gesture.
from api.dashboard.models.controls.control_request import ControlRequestBody
from harness.models import ControlRequest, Interrupt
from domain.ids import SessionId


class InterruptRequest(ControlRequestBody):
    def request(self, session_id: SessionId) -> ControlRequest:
        return Interrupt(session_id, self.request_id)
