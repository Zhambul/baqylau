"""Rich typed resources for a running Baqylau application."""

from __future__ import annotations

import base64
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar
from urllib.parse import quote, urlencode

from pydantic import TypeAdapter

from api.application.models.harnesses.harness_catalog_response import (
    HarnessCatalogResponse,
)
from api.application.models.harnesses.harness_description_response import (
    HarnessDescriptionResponse,
)
from api.application.models.insights.application_insights_response import (
    ApplicationInsightsResponse,
)
from api.application.models.files.upload_response import UploadResponse
from api.application.models.preferences.global_application_response import (
    GlobalApplicationResponse,
)
from api.application.models.preferences.session_application_response import (
    SessionApplicationResponse,
)
from api.application.models.resume.resumable_session_response import (
    ResumableSessionResponse,
)
from api.common.models.replies.health_response import HealthResponse
from api.common.models.replies.saved_response import SavedResponse
from api.controls.models.attachment_reference import AttachmentReferenceBody
from api.controls.models.control_outcome_response import ControlOutcomeResponse
from api.controls.models.launch_response import LaunchResponse
from api.diagnostics.models import DiagnosticsCheckpointResponse, DiagnosticsReportResponse
from api.sessiondata.models.entry import EntryPageResponse, EntryResponse, MessageBodyResponse
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
SESSION_APPLICATION = TypeAdapter(SessionApplicationResponse)
HARNESS_LIST = TypeAdapter(tuple[HarnessDescriptionResponse, ...])
HARNESS_CATALOG = TypeAdapter(HarnessCatalogResponse)
INSIGHTS = TypeAdapter(ApplicationInsightsResponse)
RESUMABLE_SESSIONS = TypeAdapter(tuple[ResumableSessionResponse, ...])
UPLOAD = TypeAdapter(UploadResponse)
SAVED = TypeAdapter(SavedResponse)
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
        resume_session_id: str | None = None,
        attachments: tuple[AttachmentReferenceBody, ...] = (),
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
                "resume_session_id": resume_session_id,
                "attachments": [item.model_dump() for item in attachments],
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

    def wait_for_prompt_owner(
        self,
        source: SessionRef,
        *,
        prompt: str,
        after_cursor: int,
        timeout: float,
    ) -> SessionRef:
        """Find the session that accepted a prompt after an in-place action.

        Most harnesses keep the current native session. A harness can instead
        continue under a new native id. The new session states that relation,
        so callers do not need a harness-specific branch.
        """
        candidates: list[str] = []

        def owner() -> SessionRef | None:
            nonlocal candidates
            listed = self.list()
            candidates = [source.session_id]
            candidates.extend(
                item.session.session_id
                for item in listed.sessions
                if item.session.continued_from == source.session_id
            )
            matches: list[str] = []
            for session_id in candidates:
                snapshot = self.snapshot(SessionRef(session_id))
                lower_bound = after_cursor if session_id == source.session_id else 0
                prompts = [
                    entry
                    for entry in snapshot.entries
                    if entry.cursor > lower_bound
                    and isinstance(entry.body, MessageBodyResponse)
                    and entry.body.role == "user"
                    and entry.body.phase == "prompt"
                    and entry.body.content.text.strip() == prompt
                ]
                if len(prompts) > 1:
                    raise AssertionError(
                        f"session {session_id!r} has {len(prompts)} matching prompts"
                    )
                if prompts:
                    matches.append(session_id)
            if len(matches) > 1:
                raise AssertionError(f"prompt {prompt!r} belongs to multiple sessions: {matches}")
            return SessionRef(matches[0]) if matches else None

        return wait_for(
            lambda: f"prompt {prompt!r} to belong to one of sessions {candidates}",
            owner,
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

    def _control(
        self,
        session: SessionRef,
        control_name: str,
        document: dict[str, object] | None = None,
    ) -> ActionReceipt:
        cursor = self.snapshot(session).cursor
        request_id = f"e2e-{control_name}-{uuid.uuid4()}"
        path = (
            f"/api/sessions/{quote(session.session_id, safe='')}/controls/{control_name}"
        )
        body: dict[str, object] = {"request_id": request_id}
        body.update(document or {})
        status, outcome = self.transport.post(
            path,
            body,
            CONTROL,
            {200, 202, 409},
        )
        return ActionReceipt(request_id, status, outcome, cursor)

    def send(
        self,
        session: SessionRef,
        text: str,
        *,
        attachments: tuple[AttachmentReferenceBody, ...] = (),
        replace_terminal_draft: bool = False,
    ) -> ActionReceipt:
        return self._control(session, "send-text", {
            "text": text,
            "attachments": [item.model_dump() for item in attachments],
            "replace_terminal_draft": replace_terminal_draft,
        })

    def interrupt(self, session: SessionRef) -> ActionReceipt:
        return self._control(session, "interrupt")

    def background(self, session: SessionRef) -> ActionReceipt:
        return self._control(session, "background")

    def close(self, session: SessionRef) -> ActionReceipt:
        return self._control(session, "close-session")

    def rename(self, session: SessionRef, name: str) -> ActionReceipt:
        return self._control(session, "rename-session", {"name": name})

    def auto_name(self, session: SessionRef) -> ActionReceipt:
        return self._control(session, "auto-name-session")

    def open_rewind(self, session: SessionRef) -> ActionReceipt:
        return self._control(session, "open-rewind")

    def apply_rewind(
        self,
        session: SessionRef,
        *,
        target_message_id: str,
        target_text: str,
        newer_prompt_count: int,
        mode: str,
    ) -> ActionReceipt:
        return self._control(session, "apply-rewind", {
            "target_message_id": target_message_id,
            "target_text": target_text,
            "newer_prompt_count": newer_prompt_count,
            "mode": mode,
        })

    def compact(self, session: SessionRef) -> ActionReceipt:
        return self._control(session, "compact")

    def select_model(self, session: SessionRef, model: str) -> ActionReceipt:
        return self._control(session, "select-model", {"model_id": model})

    def select_effort(self, session: SessionRef, effort: str) -> ActionReceipt:
        return self._control(session, "select-effort", {"effort": effort})

    def answer_question(
        self,
        session: SessionRef,
        *,
        attention_id: str,
        answers: tuple[dict[str, object], ...],
    ) -> ActionReceipt:
        return self._control(session, "answer-question", {
            "attention_id": attention_id,
            "decision": "answer",
            "answers": answers,
        })

    def discuss_question(
        self,
        session: SessionRef,
        *,
        attention_id: str,
        discussion: str,
    ) -> ActionReceipt:
        return self._control(session, "answer-question", {
            "attention_id": attention_id,
            "decision": "discuss",
            "discussion": discussion,
        })

    def read_plan_choices(self, session: SessionRef, attention_id: str) -> ActionReceipt:
        return self._control(
            session,
            "read-plan-choices",
            {"attention_id": attention_id},
        )

    def decide_plan(
        self,
        session: SessionRef,
        *,
        attention_id: str,
        decision: str,
        feedback: str | None = None,
    ) -> ActionReceipt:
        return self._control(session, "decide-plan", {
            "attention_id": attention_id,
            "decision": decision,
            "feedback": feedback,
        })


class HarnessesResource:
    def __init__(self, transport: HttpTransport) -> None:
        self.transport = transport

    def list(self) -> tuple[HarnessDescriptionResponse, ...]:
        return self.transport.get("/api/harnesses", HARNESS_LIST)

    def catalog(
        self,
        harness: str,
        *,
        session: SessionRef | None = None,
        workspace: str | None = None,
    ) -> HarnessCatalogResponse:
        query = urlencode({
            key: value
            for key, value in {
                "session_id": session.session_id if session is not None else None,
                "working_directory": workspace,
            }.items()
            if value is not None
        })
        suffix = f"?{query}" if query else ""
        return self.transport.get(
            f"/api/harnesses/{quote(harness, safe='')}/catalog{suffix}",
            HARNESS_CATALOG,
        )


class InsightsResource:
    def __init__(self, transport: HttpTransport) -> None:
        self.transport = transport

    def state(self) -> ApplicationInsightsResponse:
        return self.transport.get("/api/insights", INSIGHTS)

    def resumable_sessions(
        self,
        *,
        workspace: str,
        search: str | None = None,
    ) -> tuple[ResumableSessionResponse, ...]:
        query = urlencode({
            key: value
            for key, value in {"working_directory": workspace, "search": search}.items()
            if value is not None
        })
        return self.transport.get(f"/api/resumable-sessions?{query}", RESUMABLE_SESSIONS)


class UploadsResource:
    def __init__(self, transport: HttpTransport) -> None:
        self.transport = transport

    def stage(
        self,
        *,
        name: str,
        media_type: str,
        data: bytes,
        session: SessionRef | None = None,
    ) -> UploadResponse:
        _status, response = self.transport.post(
            "/api/application/uploads",
            {
                "name": name,
                "mime": media_type,
                "data": base64.b64encode(data).decode("ascii"),
                "session_id": session.session_id if session is not None else None,
            },
            UPLOAD,
            {200},
        )
        return response


class PreferencesResource:
    def __init__(self, transport: HttpTransport, application: ApplicationResource) -> None:
        self.transport = transport
        self.application = application

    def global_state(self) -> GlobalApplicationResponse:
        return self.application.state()

    def session_state(self, session: SessionRef) -> SessionApplicationResponse:
        session_id = quote(session.session_id, safe="")
        return self.transport.get(
            f"/api/sessions/{session_id}/application",
            SESSION_APPLICATION,
        )

    def _save(self, path: str, document: dict[str, object]) -> SavedResponse:
        _status, response = self.transport.post(path, document, SAVED, {200})
        return response

    def save_new_session_choices(
        self,
        *,
        workspace: str,
        harness: str,
        model: str,
        effort: str,
    ) -> SavedResponse:
        return self._save("/api/application/new-session-preferences", {
            "working_directory": workspace,
            "harness": harness,
            "model": model,
            "effort": effort,
        })

    def save_new_session_draft(
        self,
        *,
        workspace: str,
        text: str,
        sequence: float,
    ) -> SavedResponse:
        return self._save("/api/application/new-session-drafts", {
            "working_directory": workspace,
            "text": text,
            "sequence": sequence,
        })

    def save_composer_draft(
        self,
        session: SessionRef,
        *,
        text: str,
        origin: str,
        sequence: float,
    ) -> SavedResponse:
        session_id = quote(session.session_id, safe="")
        return self._save(f"/api/sessions/{session_id}/application/composer-draft", {
            "text": text,
            "origin": origin,
            "sequence": sequence,
        })

    def save_composer_queue(
        self,
        session: SessionRef,
        *,
        messages: tuple[str, ...],
        origin: str,
    ) -> SavedResponse:
        session_id = quote(session.session_id, safe="")
        return self._save(f"/api/sessions/{session_id}/application/composer-queue", {
            "items": [{"text": message} for message in messages],
            "origin": origin,
        })

    def set_view_mode(self, session: SessionRef, view_mode: str) -> SavedResponse:
        session_id = quote(session.session_id, safe="")
        return self._save(
            f"/api/sessions/{session_id}/application/view-mode",
            {"view_mode": view_mode},
        )

    def set_notifications_muted(self, session: SessionRef, muted: bool) -> SavedResponse:
        session_id = quote(session.session_id, safe="")
        return self._save(
            f"/api/sessions/{session_id}/application/notifications-muted",
            {"muted": muted},
        )

    def set_tasks_hidden(self, session: SessionRef, hidden: bool) -> SavedResponse:
        session_id = quote(session.session_id, safe="")
        return self._save(
            f"/api/sessions/{session_id}/application/tasks-hidden",
            {"hidden": hidden},
        )


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
        self.harnesses = HarnessesResource(self.transport)
        self.insights = InsightsResource(self.transport)
        self.uploads = UploadsResource(self.transport)
        self.preferences = PreferencesResource(self.transport, self.application)
        self.usage = UsageResource(self.application)
        self.diagnostics = DiagnosticsResource(self.transport)

    def close(self) -> None:
        self.transport.close()
