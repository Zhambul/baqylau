"""Control outcomes to the control plane's models.

The harness layer answers with a base result or one of five extensions; each has
a model here that mirrors it, and this picks the right one. The extensions are
tested BEFORE the base, because every one of them IS a ControlResult.
"""

from __future__ import annotations

from api.common.mapper import values
from api.controls.models.control_outcome_response import (
    CommandResultResponse,
    ControlOutcomeResponse,
    ControlResultResponse,
    DeliveryResultResponse,
    MigrationResultResponse,
    PlanChoicesResultResponse,
    RewindResultResponse,
)
from api.controls.models.launch_response import LaunchResponse
from harness.models import (
    CommandResult,
    ControlOutcome,
    DeliveryResult,
    LaunchResult,
    MigrationResult,
    PlanChoicesResult,
    RewindResult,
)


def launch(result: LaunchResult) -> LaunchResponse:
    return LaunchResponse(
        status=result.status, window_id=result.window_id, reason=result.reason
    )


def control_outcome(outcome: ControlOutcome) -> ControlOutcomeResponse:
    identity, status, reason = outcome.request_id, outcome.status, outcome.reason
    if isinstance(outcome, DeliveryResult):
        return DeliveryResultResponse(
            request_id=identity,
            status=status,
            reason=reason,
            queued=outcome.queued,
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
    if isinstance(outcome, MigrationResult):
        return MigrationResultResponse(
            request_id=identity,
            status=status,
            reason=reason,
            target_account_id=outcome.target_account_id,
        )
    if isinstance(outcome, PlanChoicesResult):
        return PlanChoicesResultResponse(
            request_id=identity,
            status=status,
            reason=reason,
            choices=tuple(values.plan_choice(choice) for choice in outcome.choices),
        )
    return ControlResultResponse(request_id=identity, status=status, reason=reason)
