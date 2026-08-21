# One browser push subscription (endpoint must be https).
from typing import Annotated

from pydantic import BaseModel, Field

from api.common.models.fields import RequiredText


class PushSubscriptionKeys(BaseModel):
    p256dh: RequiredText
    auth: RequiredText


class PushSubscriptionDocument(BaseModel):
    endpoint: Annotated[str, Field(pattern=r"^https://")]
    keys: PushSubscriptionKeys


class PushSubscriptionRequest(BaseModel):
    subscription: PushSubscriptionDocument
    device_id: RequiredText
    device_label: str | None = None
