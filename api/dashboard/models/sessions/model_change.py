# A model transition the harness made on its own — the amber "switched to…"
# note on a session row.
from typing import Literal

from pydantic import BaseModel

from api.common.models.values.model_reference import ModelReferenceResponse

class ModelChangeResponse(BaseModel):
    previous: ModelReferenceResponse | None
    current: ModelReferenceResponse
    reason: Literal["selected", "automatic_fallback", "account_migration", "reported_by_harness"]
