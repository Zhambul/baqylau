# Launch (or resume) a harness session from the new-session form.
from pydantic import BaseModel

from api.common.models.fields import RequiredText
from api.controls.models.attachment_reference import AttachmentReferenceBody, references
from harness.models import LaunchRequest
from domain.ids import AccountId, ModelId, SessionId


class LaunchSessionRequest(BaseModel):
    harness: RequiredText
    working_directory: RequiredText
    initial_text: str | None = None
    model_id: str | None = None
    effort: str | None = None
    account_id: str | None = None
    resume_session_id: str | None = None
    attachments: tuple[AttachmentReferenceBody, ...] = ()

    def request(self) -> LaunchRequest:
        return LaunchRequest(
            working_directory=self.working_directory,
            initial_text=self.initial_text,
            model_id=ModelId(self.model_id) if self.model_id else None,
            effort=self.effort,
            account_id=AccountId(self.account_id) if self.account_id else None,
            resume_session_id=(
                SessionId(self.resume_session_id) if self.resume_session_id else None
            ),
            attachments=references(self.attachments),
        )
