# Hide one directory from the session list.
from pydantic import BaseModel


class HideDirectoryRequest(BaseModel):
    working_directory: str
