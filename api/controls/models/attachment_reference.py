# One staged attachment riding a launch or a send-text gesture.
from pydantic import BaseModel

from api.common.models.fields import RequiredText
from harness.models import AttachmentReference


class AttachmentReferenceBody(BaseModel):
    local_path: RequiredText
    display_name: RequiredText
    media_type: str | None = None

    def reference(self) -> AttachmentReference:
        return AttachmentReference(self.local_path, self.display_name, self.media_type)


def references(attachments: tuple[AttachmentReferenceBody, ...]) -> tuple[AttachmentReference, ...]:
    return tuple(attachment.reference() for attachment in attachments)
