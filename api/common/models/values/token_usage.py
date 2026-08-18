# One actor's or one model's token consumption, as the scorebar reads it.
from pydantic import BaseModel


class TokenUsageResponse(BaseModel):
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    one_hour_cache_write_tokens: int
