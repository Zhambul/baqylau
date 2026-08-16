# The staged attachment's absolute path for the @path mention.
from pydantic import BaseModel


class UploadResponse(BaseModel):
    ok: bool = True
    path: str
    name: str
    mime: str
    is_image: bool
