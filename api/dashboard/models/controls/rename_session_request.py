# The rename gesture.
from api.common.models.fields import RequiredText
from api.dashboard.models.controls.control_request import ControlRequestBody
from contracts.harness import ControlRequest, RenameSession
from domain.ids import SessionId


class RenameSessionRequest(ControlRequestBody):
    name: RequiredText

    def request(self, session_id: SessionId) -> ControlRequest:
        return RenameSession(session_id, self.request_id, name=self.name)
