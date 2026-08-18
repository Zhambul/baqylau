# What the session has spent: totals, and the same broken down by actor and by
# model. The cost is a Decimal, on the wire as a STRING — a price must not
# round-trip through a float.
from decimal import Decimal

from pydantic import BaseModel

from domain.ids import ActorId

from api.common.models.values.token_usage import TokenUsageResponse

class UsageSummaryResponse(BaseModel):
    tokens: TokenUsageResponse
    cost_in_usd: Decimal | None
    by_actor: dict[ActorId, TokenUsageResponse]
    # Keyed by the model's own native id, which is a NAME, not an identity of
    # ours — there is no ModelId to reach for.
    by_model: dict[str, TokenUsageResponse]
