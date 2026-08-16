# The telemetry-accepted reply.
from pydantic import BaseModel


class RecordedResponse(BaseModel):
    recorded: bool = True
