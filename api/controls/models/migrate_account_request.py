# The account-migration gesture.
from api.controls.models.control_request import ControlRequestBody
from harness.models import ControlRequest, MigrateAccount
from domain.ids import RequestId, SessionId


class MigrateAccountRequest(ControlRequestBody):
    def request(self, session_id: SessionId) -> ControlRequest:
        return MigrateAccount(session_id, RequestId(self.request_id))
