# The effort-selection gesture.
from api.common.models.fields import RequiredText
from api.dashboard.models.controls.control_request import ControlRequestBody
from harness.models import ControlRequest, SelectEffort
from domain.ids import SessionId


class SelectEffortRequest(ControlRequestBody):
    effort: RequiredText

    def request(self, session_id: SessionId) -> ControlRequest:
        return SelectEffort(session_id, self.request_id, effort=self.effort)
