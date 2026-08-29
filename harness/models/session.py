"""The observed session — the read-model every other message is phrased about."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from domain.ids import ActorId, SessionId, WindowId
from harness.models.raw_events import RawEventSourceContext

if TYPE_CHECKING:  # the plugin is the composition root above these messages
    from harness.contract import HarnessPlugin


@dataclass(frozen=True)
class LocatedSession:
    session_id: SessionId
    window_id: WindowId


@dataclass(frozen=True)
class Session:
    """One observed harness session — a read-model derived from committed facts.

    The row is born by the reaction to the session's own `session.started` fact;
    nothing upstream of the store ever requires one. Identity columns are written
    once; the two LIVE columns (`terminal_window_id`, `harness_process_id`) are
    kept current from the stored event of every later hook-borne fact, because a
    resumed session shows up in a new window with a new process. `plugin` is
    attachment, not identity: the server-side `SessionStore` hands out sessions
    with it set, recorder processes leave it None.
    """

    session_id: SessionId
    lead_actor_id: ActorId
    source_reference: str
    working_directory: str | None
    terminal_window_id: WindowId | None = None
    harness_process_id: int | None = None
    plugin: HarnessPlugin | None = field(default=None, compare=False, repr=False)
    project_directory: str | None = None

    @property
    def source_context(self) -> RawEventSourceContext:
        return RawEventSourceContext(
            session_id=self.session_id,
            lead_actor_id=self.lead_actor_id,
            actor_id=self.lead_actor_id,
            parent_actor_id=None,
            source_reference=self.source_reference,
        )
