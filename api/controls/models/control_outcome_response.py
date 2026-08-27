# What a control gesture DID. One model per kind of verdict, mirroring the
# harness layer's own union: a plain result, and the four that carry something
# extra. The api layer keeps its own copy so a field the controllers add is a
# deliberate change to the browser contract rather than an automatic one.
#
from typing import TypeAlias

from pydantic import BaseModel

from api.common.models.values.plan_choice import PlanChoiceResponse
from harness.models import (
    ConfirmationOutcome,
    ControlAcknowledgement,
    MessageDeliveryStatus,
)


class ControlResultResponse(BaseModel):
    """The verdict every gesture answers with, and all that most of them do."""

    request_id: str
    status: ControlAcknowledgement
    reason: str | None


class InterruptResultResponse(ControlResultResponse):
    """The result of an interrupt request."""

    restored_text: str
    corroborated: bool


class MessageDeliveryResultResponse(BaseModel):
    """The location that the harness confirmed for one message."""

    request_id: str
    status: MessageDeliveryStatus


class CommandResultResponse(ControlResultResponse):
    confirmation: ConfirmationOutcome | None


class RewindResultResponse(ControlResultResponse):
    restored_text: str
    degraded: bool


class PlanChoicesResultResponse(ControlResultResponse):
    choices: tuple[PlanChoiceResponse, ...]


ControlOutcomeResponse: TypeAlias = (
    ControlResultResponse
    | InterruptResultResponse
    | MessageDeliveryResultResponse
    | CommandResultResponse
    | RewindResultResponse
    | PlanChoicesResultResponse
)
