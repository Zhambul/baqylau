# Which account a session is billed to.
from pydantic import BaseModel


class AccountReferenceResponse(BaseModel):
    account_id: str
    display_name: str
