"""The one application-owned scheduler for plugin event sources."""

from __future__ import annotations

import threading

from contracts.harness import CheckpointStore, RawEventDelivery
from domain.ids import SessionId
from runtime.registry import HarnessRegistry, RegisteredSession

OBSERVATION_INTERVAL_SECONDS = 0.25
RECENT_SESSION_COUNT = 4


def _audit_failure(where: str, context: dict) -> None:
    """Record a swallowed observation failure, then carry on.

    Imported lazily so this module keeps its import-time purity, and guarded so a
    broken auditor can never take down the scheduler it exists to explain.
    """
    try:
        from core import audit

        audit.error(str(context.get("session_id", "")), f"observation ({where})", context)
    except Exception:
        pass


class ObservationRunner:
    def __init__(
        self,
        registry: HarnessRegistry,
        checkpoints: CheckpointStore,
        delivery: RawEventDelivery,
    ) -> None:
        self.registry = registry
        self.checkpoints = checkpoints
        self.delivery = delivery
        self._active_sessions: dict[SessionId, RegisteredSession] = {}

    def run(self, stop_event: threading.Event) -> None:
        # The scheduler thread must outlive every failure it can observe: it is the ONE
        # driver of every polled event source, and nothing restarts it. An unguarded
        # exception here once killed observation for EVERY session silently -- the
        # conversation simply stopped arriving while the sources written by separate
        # processes kept flowing, so the session still looked alive. Both loops below
        # degrade and carry on; every swallow is audited.
        while not stop_event.is_set():
            try:
                self.run_once()
            except Exception:
                _audit_failure("observation pass", {})
            stop_event.wait(OBSERVATION_INTERVAL_SECONDS)

    def run_once(self) -> None:
        for registered_session in self.registry.recently_observed_sessions(RECENT_SESSION_COUNT):
            self._active_sessions[registered_session.session.session_id] = registered_session
        for session_id, registered_session in tuple(self._active_sessions.items()):
            if self.registry.session_is_finished(session_id):
                del self._active_sessions[session_id]
                continue
            for plugin in self.registry.plugins():
                for source in plugin.events.sources(
                    registered_session.session,
                    self.checkpoints,
                ):
                    self._drain(source, session_id)
            if self.registry.session_is_finished(session_id):
                del self._active_sessions[session_id]

    def _drain(self, source, session_id: SessionId) -> None:
        # One unhappy source must never stop its siblings, nor the next session's.
        try:
            source.drain(self.delivery)
        except Exception:
            _audit_failure(
                "source drain",
                {
                    "session_id": str(session_id),
                    "source_identity": getattr(source, "source_identity", ""),
                    "source": type(source).__name__,
                },
            )
