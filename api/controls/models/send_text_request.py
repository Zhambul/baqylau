# The send-text gesture: text and/or attachments into the session.
from pydantic import model_validator

from api.controls.models.attachment_reference import AttachmentReferenceBody, references
from api.controls.models.control_request import ControlRequestBody
from harness.models import ControlRequest, SendText
from domain.ids import SessionId


class SendTextRequest(ControlRequestBody):
    text: str
    attachments: tuple[AttachmentReferenceBody, ...] = ()
    replace_terminal_draft: bool = False

    @model_validator(mode="after")
    def _text_or_attachments(self) -> "SendTextRequest":
        if not self.text and not self.attachments:
            raise ValueError("text or attachments are required")
        return self

    def request(self, session_id: SessionId) -> ControlRequest:
        return SendText(
            session_id,
            self.request_id,
            text=self.text,
            attachments=references(self.attachments),
            replace_terminal_draft=self.replace_terminal_draft,
        )
