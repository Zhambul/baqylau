# The rename gesture.
from api.common.models.fields import RequiredText
from api.controls.models.control_request import ControlRequestBody
from harness.models import ControlRequest, RenameSession
from domain.ids import SessionId


class RenameSessionRequest(ControlRequestBody):
    name: RequiredText

    def request(self, session_id: SessionId) -> ControlRequest:
        return RenameSession(session_id, self.request_id, name=self.name)
