# What a control gesture DID. One model per kind of verdict, mirroring the
# harness layer's own union: a plain result, and the five that carry something
# extra. The api layer keeps its own copy so a field the controllers add is a
# deliberate change to the browser contract rather than an automatic one.
#
from typing import Literal, TypeAlias

from pydantic import BaseModel

from api.common.models.values.plan_choice import PlanChoiceResponse
class ControlResultResponse(BaseModel):
    """The verdict every gesture answers with, and all that most of them do."""

    request_id: str
    status: Literal["acknowledged", "rejected", "indeterminate"]
    reason: str | None


class DeliveryResultResponse(ControlResultResponse):
    """send-text and interrupt. `queued` is the server's verdict that the text
    landed mid-turn; `corroborated` marks an interrupt the harness confirmed in
    its own evidence rather than one read off its screen."""

    queued: bool
    restored_text: str
    corroborated: bool


class CommandResultResponse(ControlResultResponse):
    confirmation: Literal["confirmed", "not_needed", "failed"] | None


class RewindResultResponse(ControlResultResponse):
    restored_text: str
    degraded: bool


class MigrationResultResponse(ControlResultResponse):
    target_account_id: str | None


class PlanChoicesResultResponse(ControlResultResponse):
    choices: tuple[PlanChoiceResponse, ...]


ControlOutcomeResponse: TypeAlias = (
    ControlResultResponse
    | DeliveryResultResponse
    | CommandResultResponse
    | RewindResultResponse
    | MigrationResultResponse
    | PlanChoicesResultResponse
)
