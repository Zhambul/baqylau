"""Claude Code's reactions to its own committed facts, run by the interpreter.

The interpreter dispatches by the event's harness, so no implementation here
carries a harness check. Instances live on the plugin descriptor (built at
import with no dependencies); the control service arrives per call, because it
only exists inside the daemon.
"""

from __future__ import annotations

from contracts.harness import (
    HarnessCanonicalEventReactor,
    HarnessReactorContext,
    MigrateAccount,
)
from domain.events import CanonicalEvent, GoalChanged, SessionStarted
from plugins.claude_code.otel import launch as otel


class ClaudeOtelCanonicalEventReactor(HarnessCanonicalEventReactor):
    def react(
        self, canonical_event: CanonicalEvent, controls: HarnessReactorContext
    ) -> None:
        if isinstance(canonical_event.payload, SessionStarted):
            otel.start()


class ClaudeAccountMigrationCanonicalEventReactor(HarnessCanonicalEventReactor):
    def react(
        self, canonical_event: CanonicalEvent, controls: HarnessReactorContext
    ) -> None:
        payload = canonical_event.payload
        if isinstance(payload, GoalChanged) and payload.state == "usage_limited" \
                and canonical_event.parent_actor_id is None:
            controls.execute(MigrateAccount(
                canonical_event.session_id,
                f"claude_code:rate_limit:{canonical_event.event_id}",
            ))
