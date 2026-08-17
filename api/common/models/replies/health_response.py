# Who is answering on this port.
from pydantic import BaseModel


class HealthResponse(BaseModel):
    process_id: int
