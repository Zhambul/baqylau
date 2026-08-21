"""The one finish signal every session has, wrapped or not: the CLI process died."""

from __future__ import annotations

import time

from core.process import process_alive, process_is_alive
from domain.ids import HarnessName, RawEventId
from harness.contract import HarnessRawEventSource
from harness.models import LIVENESS_SOURCE_TYPE, RawEvent, Session
from repository.mapper.documents import encode_document
from harness.models.directives import ProcessExit


class ProcessProbe:
    """The per-tick liveness check, kept cheap.

    The `ps` name check exists only to catch a pid recorded before a daemon
    restart and reused while nobody was watching. Reuse requires a death this
    probe would have seen, so the name is confirmed ONCE per source identity
    and every later probe is a signal-0 syscall. Before this memory existed the
    check was a `ps` SUBPROCESS per unfinished session per 0.25 s tick, and on
    macOS every fork stalls the whole process on its malloc locks — measured as
    0.3–1 s of latency on every HTTP request the daemon served. The memory
    lives here, on the interpreter, because the sources themselves are rebuilt
    every tick.
    """

    def __init__(self) -> None:
        self._verified: set[str] = set()

    def alive(self, identity: str, process_id: int, process_name: str) -> bool:
        if identity in self._verified:
            if process_is_alive(process_id):
                return True
            self._verified.discard(identity)
            return False
        if not process_alive(process_id, process_name):
            return False
        self._verified.add(identity)
        return True


class SessionLivenessSource(HarnessRawEventSource):
    """Built by the interpreter for every unfinished session. Emits ONE raw
    event when the CLI process is gone — the one finish signal every session
    has, wrapped or not.

    Position encoding: a latch — `exited` means the exit was already recorded.
    """

    def __init__(self, session: Session, process_probe: ProcessProbe) -> None:
        if session.harness_process_id is None:
            # Never swallowed: the failure lands in the source-construction
            # audit every tick until the pid arrives.
            raise ValueError(f"session has no harness process id: {session.session_id}")
        if session.plugin is None:
            # The same guarantee, for the same reason. `Session.plugin` is
            # attachment rather than identity — a recorder process leaves it
            # None — and this source reads the harness name and its process
            # name off it on every tick. Constructing one from a detached
            # session was already an AttributeError at the first read; it is
            # now a named failure at the point the mistake is made.
            raise ValueError(f"session has no attached harness plugin: {session.session_id}")
        self.session = session
        self.process_probe = process_probe
        # Held narrowed: the checks above are what make these safe, and
        # re-reading them off `self.session` below would discard that.
        self.plugin = session.plugin
        self.harness_process_id = session.harness_process_id
        self.source_identity = (
            f"{self.plugin.info.name}:liveness:"
            f"{session.session_id}:{self.harness_process_id}"
        )

    def read(self, after_position: str | None) -> tuple[RawEvent, ...]:
        if after_position == "exited":
            return ()
        if self.process_probe.alive(
            self.source_identity,
            self.harness_process_id,
            self.plugin.info.cli_process_name,
        ):
            return ()
        return (RawEvent(
            raw_event_id=RawEventId(self.source_identity),
            harness=HarnessName(self.plugin.info.name),
            source_type=LIVENESS_SOURCE_TYPE,
            source_name=f"process:{self.harness_process_id}",
            source_position="exited",
            session_id=self.session.session_id,
            actor_id=self.session.lead_actor_id,
            parent_actor_id=None,
            observed_at=time.time(),
            encoding="json",
            payload=encode_document(
                ProcessExit(process_id=self.harness_process_id, state="exited")
            ),
            source_identity=self.source_identity,
        ),)
