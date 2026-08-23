"""Rich typed resources for a running Baqylau application."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar
from urllib.parse import quote, urlencode

from pydantic import TypeAdapter

from api.application.models.preferences.global_application_response import (
    GlobalApplicationResponse,
)
from api.common.models.replies.health_response import HealthResponse
from api.controls.models.control_outcome_response import ControlOutcomeResponse
from api.controls.models.launch_response import LaunchResponse
from api.diagnostics.models import DiagnosticsCheckpointResponse, DiagnosticsReportResponse
from api.sessiondata.models.entry import EntryPageResponse, EntryResponse
from api.sessiondata.models.session_data import (
    SessionDataListResponse,
    SessionDataResponse,
)
from sdk.state import SessionSnapshot
from sdk.transport import ApiFailure, HttpTransport

T = TypeVar("T")

HEALTH = TypeAdapter(HealthResponse)
LAUNCH = TypeAdapter(LaunchResponse)
CONTROL: TypeAdapter[ControlOutcomeResponse] = TypeAdapter(ControlOutcomeResponse)
SESSION_LIST = TypeAdapter(SessionDataListResponse)
SESSION_DATA = TypeAdapter(SessionDataResponse)
ENTRY_PAGE = TypeAdapter(EntryPageResponse)
APPLICATION = TypeAdapter(GlobalApplicationResponse)
DIAGNOSTICS_CHECKPOINT = TypeAdapter(DiagnosticsCheckpointResponse)
DIAGNOSTICS_REPORT = TypeAdapter(DiagnosticsReportResponse)


@dataclass(frozen=True)
class LaunchRef:
    harness: str
    workspace: str
    window_id: str
    known_session_ids: frozenset[str]


@dataclass(frozen=True)
class SessionRef:
    session_id: str


@dataclass(frozen=True)
class ActionReceipt:
    request_id: str
    status_code: int
    outcome: ControlOutcomeResponse
    cursor_before: int


class WaitTimeout(AssertionError):
    pass


def wait_for(
    description: str | Callable[[], str],
    read: Callable[[], T | None],
    *,
    timeout: float,
    interval: float = 0.5,
) -> T:
    deadline = time.monotonic() + timeout
    while True:
        found = read()
        if found is not None and found is not False:
            return found
        if time.monotonic() >= deadline:
            detail = description() if callable(description) else description
            raise WaitTimeout(f"timed out after {timeout:.0f}s waiting for {detail}")
        time.sleep(interval)


class ApplicationResource:
    def __init__(self, transport: HttpTransport) -> None:
        self.transport = transport

    def health(self) -> HealthResponse:
        return self.transport.get("/api/health", HEALTH)

    def wait_until_ready(self, timeout: float = 30.0) -> HealthResponse:
        last_error: list[str] = []

        def ready() -> HealthResponse | None:
            try:
                return self.health()
            except (ApiFailure, OSError) as error:
                last_error[:] = [str(error)]
                return None

        return wait_for(
            lambda: "the application health response; last error: "
            + (last_error[-1] if last_error else "none"),
            ready,
            timeout=timeout,
            interval=0.1,
        )

    def state(self) -> GlobalApplicationResponse:
        return self.transport.get("/api/application", APPLICATION)


class SessionWatch:
    def __init__(self, sessions: SessionsResource, session: SessionRef) -> None:
        self.sessions = sessions
        self.session = session
        self.last_snapshot: SessionSnapshot | None = None

    def snapshot(self) -> SessionSnapshot:
        self.last_snapshot = self.sessions.snapshot(self.session)
        return self.last_snapshot

    def wait(
        self,
        description: str | Callable[[SessionSnapshot], str],
        condition: Callable[[SessionSnapshot], T | None],
        *,
        timeout: float,
    ) -> T:
        def read() -> T | None:
            return condition(self.snapshot())

        def detail() -> str:
            snapshot = self.last_snapshot or self.snapshot()
            return description(snapshot) if callable(description) else description

        return wait_for(detail, read, timeout=timeout)


class SessionsResource:
    def __init__(self, transport: HttpTransport) -> None:
        self.transport = transport

    def list(self) -> SessionDataListResponse:
        return self.transport.get("/sessionData", SESSION_LIST)

    def launch(
        self,
        harness: str,
        *,
        workspace: str,
        prompt: str | None,
        model: str | None,
        effort: str | None,
    ) -> LaunchRef:
        known = frozenset(item.session.session_id for item in self.list().sessions)
        status, answer = self.transport.post(
            "/api/sessions",
            {
                "harness": harness,
                "working_directory": workspace,
                "initial_text": prompt,
                "model_id": model,
                "effort": effort,
            },
            LAUNCH,
            {202, 409},
        )
        if status != 202 or answer.window_id is None:
            raise ApiFailure(f"session launch was rejected: {answer.reason or answer.status}")
        return LaunchRef(harness, workspace, str(answer.window_id), known)

    def wait_for_session(self, launch: LaunchRef, timeout: float = 120.0) -> SessionRef:
        candidates: list[str] = []

        def announced() -> SessionRef | None:
            nonlocal candidates
            candidates = [
                item.session.session_id
                for item in self.list().sessions
                if item.session.session_id not in launch.known_session_ids
                and item.session.harness == launch.harness
                and item.session.working_directory == launch.workspace
            ]
            if len(candidates) > 1:
                raise AssertionError(
                    f"launch window {launch.window_id!r} has multiple new sessions: {candidates}"
                )
            return SessionRef(candidates[0]) if candidates else None

        return wait_for(
            lambda: f"launch window {launch.window_id!r} to announce one session; found {candidates}",
            announced,
            timeout=timeout,
        )

    def snapshot(self, session: SessionRef) -> SessionSnapshot:
        session_id = quote(session.session_id, safe="")
        data = self.transport.get(f"/sessionData/{session_id}", SESSION_DATA)
        pages: list[tuple[EntryResponse, ...]] = []
        before: int | None = None
        while True:
            parameters: dict[str, int] = {"limit": 1000, "at": data.cursor}
            if before is not None:
                parameters["before"] = before
            page = self.transport.get(
                f"/sessionData/{session_id}/entries?{urlencode(parameters)}",
                ENTRY_PAGE,
            )
            pages.append(page.items)
            if not page.has_more:
                break
            if not page.items:
                raise ApiFailure("the entry feed reports another page but returned no entries")
            next_before = page.oldest_cursor
            if before is not None and next_before >= before:
                raise ApiFailure(
                    f"the entry feed did not move back from cursor {before} to {next_before}"
                )
            before = next_before
        entries = tuple(entry for page_items in reversed(pages) for entry in page_items)
        return SessionSnapshot(data=data, entries=entries)

    def watch(self, session: SessionRef) -> SessionWatch:
        return SessionWatch(self, session)

    def wait_until_finished(self, session: SessionRef, timeout: float) -> SessionSnapshot:
        return self.watch(session).wait(
            lambda snapshot: (
                f"session {session.session_id!r} and all its actors to finish; "
                f"session state is {snapshot.data.session.state!r}, actor states are "
                f"{[actor.state for actor in snapshot.data.actors]}"
            ),
            lambda snapshot: (
                snapshot
                if snapshot.data.session.state == "finished"
                and all(actor.state == "finished" for actor in snapshot.data.actors)
                else None
            ),
            timeout=timeout,
        )

    def send(self, session: SessionRef, text: str) -> ActionReceipt:
        cursor = self.snapshot(session).cursor
        request_id = f"e2e-send-{uuid.uuid4()}"
        path = f"/api/sessions/{quote(session.session_id, safe='')}/controls/send-text"
        status, outcome = self.transport.post(
            path,
            {"request_id": request_id, "text": text},
            CONTROL,
            {200, 202, 409},
        )
        return ActionReceipt(request_id, status, outcome, cursor)

    def background(self, session: SessionRef) -> ActionReceipt:
        cursor = self.snapshot(session).cursor
        request_id = f"e2e-background-{uuid.uuid4()}"
        path = f"/api/sessions/{quote(session.session_id, safe='')}/controls/background"
        status, outcome = self.transport.post(
            path,
            {"request_id": request_id},
            CONTROL,
            {200, 202, 409},
        )
        return ActionReceipt(request_id, status, outcome, cursor)

    def close(self, session: SessionRef) -> ActionReceipt:
        cursor = self.snapshot(session).cursor
        request_id = f"e2e-close-{uuid.uuid4()}"
        path = f"/api/sessions/{quote(session.session_id, safe='')}/controls/close-session"
        status, outcome = self.transport.post(
            path,
            {"request_id": request_id},
            CONTROL,
            {200, 202, 409},
        )
        return ActionReceipt(request_id, status, outcome, cursor)


class UsageResource:
    def __init__(self, application: ApplicationResource) -> None:
        self.application = application

    def state(self) -> GlobalApplicationResponse:
        return self.application.state()


class DiagnosticsResource:
    def __init__(self, transport: HttpTransport) -> None:
        self.transport = transport

    def checkpoint(self) -> DiagnosticsCheckpointResponse:
        return self.transport.get("/api/diagnostics/checkpoint", DIAGNOSTICS_CHECKPOINT)

    def report(
        self,
        start: DiagnosticsCheckpointResponse,
        end: DiagnosticsCheckpointResponse,
    ) -> DiagnosticsReportResponse:
        query = urlencode({
            "after_raw_event": start.raw_event_cursor,
            "through_raw_event": end.raw_event_cursor,
            "after_audit_error": start.audit_error_cursor,
            "through_audit_error": end.audit_error_cursor,
        })
        return self.transport.get(f"/api/diagnostics/report?{query}", DIAGNOSTICS_REPORT)

    def wait_until_drained(self, timeout: float = 30.0) -> DiagnosticsCheckpointResponse:
        previous_raw = -1
        stable_reads = 0

        def drained() -> DiagnosticsCheckpointResponse | None:
            nonlocal previous_raw, stable_reads
            found = self.checkpoint()
            stable_reads = stable_reads + 1 if found.raw_event_cursor == previous_raw else 0
            previous_raw = found.raw_event_cursor
            complete = (
                found.pending_raw_event_count == 0
                and found.reaction_cursor >= found.canonical_cursor
                and stable_reads >= 2
            )
            return found if complete else None

        return wait_for("the event pipeline to drain", drained, timeout=timeout)


class BaqylauClient:
    def __init__(self, base_url: str) -> None:
        self.transport = HttpTransport(base_url)
        self.application = ApplicationResource(self.transport)
        self.sessions = SessionsResource(self.transport)
        self.usage = UsageResource(self.application)
        self.diagnostics = DiagnosticsResource(self.transport)

    def close(self) -> None:
        self.transport.close()
