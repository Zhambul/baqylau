# The apply-rewind gesture.
from api.common.models.fields import RequiredText
from api.dashboard.models.controls.control_request import ControlRequestBody
from harness.models import ApplyRewind, ControlRequest
from domain.ids import MessageId, SessionId


class ApplyRewindRequest(ControlRequestBody):
    target_message_id: RequiredText
    target_text: RequiredText
    newer_prompt_count: int = 0
    mode: RequiredText

    def request(self, session_id: SessionId) -> ControlRequest:
        return ApplyRewind(
            session_id,
            self.request_id,
            target_message_id=MessageId(self.target_message_id),
            target_text=self.target_text,
            newer_prompt_count=self.newer_prompt_count,
            mode=self.mode,
        )
