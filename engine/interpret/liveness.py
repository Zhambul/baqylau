"""The one finish signal every session has, wrapped or not: the CLI process died."""

from __future__ import annotations

import json
import time

from core.process import process_alive
from domain.ids import RawEventId
from harness.contract import HarnessRawEventSource
from harness.models import LIVENESS_SOURCE_TYPE, RawEvent, Session


class SessionLivenessSource(HarnessRawEventSource):
    """Built by the interpreter for every unfinished session. Emits ONE raw
    event when the CLI process is gone — the one finish signal every session
    has, wrapped or not.

    Position encoding: a latch — `exited` means the exit was already recorded.
    """

    def __init__(self, session: Session) -> None:
        if session.harness_process_id is None:
            # Never swallowed: the failure lands in the source-construction
            # audit every tick until the pid arrives.
            raise ValueError(f"session has no harness process id: {session.session_id}")
        self.session = session
        self.source_identity = (
            f"{session.plugin.info.name}:liveness:"
            f"{session.session_id}:{session.harness_process_id}"
        )

    def read(self, after_position: str | None) -> tuple[RawEvent, ...]:
        if after_position == "exited":
            return ()
        if process_alive(
            self.session.harness_process_id,
            self.session.plugin.info.cli_process_name,
        ):
            return ()
        return (RawEvent(
            raw_event_id=RawEventId(self.source_identity),
            harness=self.session.plugin.info.name,
            source_type=LIVENESS_SOURCE_TYPE,
            source_name=f"process:{self.session.harness_process_id}",
            source_position="exited",
            session_id=self.session.session_id,
            actor_id=self.session.lead_actor_id,
            parent_actor_id=None,
            observed_at=time.time(),
            encoding="json",
            payload=json.dumps(
                {"process_id": self.session.harness_process_id, "state": "exited"}
            ).encode("utf-8"),
            source_identity=self.source_identity,
        ),)
