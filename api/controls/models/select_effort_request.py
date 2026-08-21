# The effort-selection gesture.
from api.common.models.fields import RequiredText
from api.controls.models.control_request import ControlRequestBody
from harness.models import SelectEffort
from domain.ids import RequestId, SessionId


class SelectEffortRequest(ControlRequestBody):
    effort: RequiredText

    def request(self, session_id: SessionId) -> SelectEffort:
        return SelectEffort(session_id, RequestId(self.request_id), effort=self.effort)
