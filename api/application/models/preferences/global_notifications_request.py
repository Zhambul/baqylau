# The global notification switch.
from pydantic import BaseModel


class GlobalNotificationsRequest(BaseModel):
    enabled: bool
