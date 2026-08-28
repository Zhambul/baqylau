"""Claude Code's reactions to its own committed facts, run by the interpreter.

The interpreter dispatches by the event's harness, so no implementation here
carries a harness check. Instances live on the plugin descriptor (built at
import with no dependencies); the control service arrives per call, because it
only exists inside the daemon.
"""

from __future__ import annotations

from domain.events import CanonicalEvent, EventPayload, SessionStarted
from harness.contract import HarnessCanonicalEventReactor, HarnessReactorContext
from harness.impl.claude_code.otel import launch as otel


class ClaudeOtelCanonicalEventReactor(HarnessCanonicalEventReactor):
    def react(
        self, canonical_event: CanonicalEvent[EventPayload], harness_reactor_context: HarnessReactorContext
    ) -> None:
        if isinstance(canonical_event.payload, SessionStarted):
            otel.start()
