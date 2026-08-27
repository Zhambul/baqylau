"""Control outcomes to the control plane's models.

The harness layer answers with a base result or one of four extensions; each has
a model here that mirrors it, and this picks the right one. The extensions are
tested BEFORE the base, because every one of them IS a ControlResult.
"""

from __future__ import annotations

from api.common.mapper import values
from api.controls.models.control_outcome_response import (
    CommandResultResponse,
    ControlOutcomeResponse,
    ControlResultResponse,
    InterruptResultResponse,
    MessageDeliveryResultResponse,
    PlanChoicesResultResponse,
    RewindResultResponse,
)
from api.controls.models.launch_response import LaunchResponse
from harness.models import (
    CommandResult,
    ControlOutcome,
    InterruptResult,
    LaunchResult,
    MessageDeliveryResult,
    PlanChoicesResult,
    RewindResult,
)


def launch(launch_result: LaunchResult) -> LaunchResponse:
    return LaunchResponse(
        status=launch_result.status,
        window_id=launch_result.window_id,
        reason=launch_result.reason,
    )


def control_outcome(outcome: ControlOutcome) -> ControlOutcomeResponse:
    if isinstance(outcome, MessageDeliveryResult):
        return MessageDeliveryResultResponse(
            request_id=outcome.request_id,
            status=outcome.status,
        )
    identity, status = outcome.request_id, outcome.status
    reason = outcome.reason
    if isinstance(outcome, InterruptResult):
        return InterruptResultResponse(
            request_id=identity,
            status=status,
            reason=reason,
            restored_text=outcome.restored_text,
            corroborated=outcome.corroborated,
        )
    if isinstance(outcome, CommandResult):
        return CommandResultResponse(
            request_id=identity,
            status=status,
            reason=reason,
            confirmation=outcome.confirmation,
        )
    if isinstance(outcome, RewindResult):
        return RewindResultResponse(
            request_id=identity,
            status=status,
            reason=reason,
            restored_text=outcome.restored_text,
            degraded=outcome.degraded,
        )
    if isinstance(outcome, PlanChoicesResult):
        return PlanChoicesResultResponse(
            request_id=identity,
            status=status,
            reason=reason,
            choices=tuple(values.plan_choice(choice) for choice in outcome.choices),
        )
    return ControlResultResponse(request_id=identity, status=status, reason=reason)
