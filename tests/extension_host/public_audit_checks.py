# Copyright (c) 2026 Zhambyl Yermagambet
"""Read a raw event's processing steps through the public diagnostics API (C06, C09)."""

from api.diagnostics.raw_event_audit_models import RawEventAuditStepResponse
from sdk.client import BaqylauClient
from tests.extension_host import lifecycle_daemon_checks as checks, lifecycle_daemon_fixture as fixture


def public_audit(client: BaqylauClient, case: fixture.LifecycleDaemon) -> tuple[RawEventAuditStepResponse, ...]:
    """Read the one raw event's steps, and require the journal's stages in the same order.

    Returns:
        The public steps.

    """
    audit = client.diagnostics.raw_event_audit(case.raw_event_ids()[0])
    stored = checks.step_stages(checks.only_journal(case))
    assert tuple(step.stage for step in audit.steps) == stored
    return audit.steps


def public_step(client: BaqylauClient, case: fixture.LifecycleDaemon, stage: str) -> RawEventAuditStepResponse:
    """Read the one public step of an extension stage.

    Returns:
        The step with its owner, outcome, diagnostic code, and operation kinds.

    """
    selected = [step for step in public_audit(client, case) if step.stage == stage]
    assert len(selected) == 1
    return selected[0]
