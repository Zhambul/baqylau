# The account-migration gesture.
from api.dashboard.models.controls.control_request import ControlRequestBody
from contracts.harness import ControlRequest, MigrateAccount
from domain.ids import SessionId


class MigrateAccountRequest(ControlRequestBody):
    def request(self, session_id: SessionId) -> ControlRequest:
        return MigrateAccount(session_id, self.request_id)
