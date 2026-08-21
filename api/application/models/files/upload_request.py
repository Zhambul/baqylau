# One composer attachment as a JSON+base64 document (no multipart on purpose).
from pydantic import BaseModel


class UploadRequest(BaseModel):
    name: str
    mime: str = ""
    data: str
    session_id: str | None = None
