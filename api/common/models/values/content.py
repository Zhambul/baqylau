# Text a harness produced, and how to draw it. The media type rides along
# because whether a message is markdown is a fact the harness told us — a body
# that carried only the string would leave every client guessing by role.
from pydantic import BaseModel

from domain.values import MediaType


class ContentResponse(BaseModel):
    text: str
    media_type: MediaType
