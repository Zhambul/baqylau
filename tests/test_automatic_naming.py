"""Durable jobs, title safety, and generic naming semantics."""

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys
from typing import cast

import pytest

from audit.recorder import AuditRecorder
from domain.entries import MessageBody, SessionEntry
from domain.events import CanonicalEvent, EventPayload, MessageCreated, SessionTitleChanged
from domain.ids import ActorId, CanonicalEventId, HarnessName, MessageId, RequestId, SessionId, WindowId
from domain.naming import NamingJob, NamingJobState
from domain.sessiondata import SessionData
from domain.values import MessagePhase, MessageRole, TextContent, TitleOrigin
from engine.interpret.translators import AutomaticTitleTranslator
from harness.impl.claude_code.plugin import plugin as claude_plugin
from harness.impl.codex.plugin import plugin as codex_plugin
from harness.contract import ControlHandler, HarnessController, HarnessPlugin
from harness.models import (
    AutoNameSession,
    ControlAcknowledgement,
    ControlContext,
    ControlName,
    ControlRequest,
    ControlResult,
    DurableTitleResult,
    InterruptRegistry,
    RawEvent,
    RenameSession,
    Session,
)
from harness.registry import HarnessRegistry, HarnessRegistryError
from harness.services.control_effects import ControlEffectRecorder
from harness.services.controls import HarnessControlService
from inference.contract import ModelFactory, ModelPromptRequest, ModelPromptResponse
from inference.errors import ModelUnavailableError
from naming.jobs import AutomaticNamingReaction
from naming.service import AutomaticSessionNamer, normalize_title
from repository.contract.facts import RawEventRepository
from repository.contract.session_data import SessionDataRepository
from repository.contract.sessions import SessionRepository
from repository.impl.sqlite.databases import main_database
from repository.impl.sqlite.naming import SqliteNamingJobRepository
from repository.impl.sqlite.schema import MAIN_SCHEMA_VERSION
from terminal.adapter import TerminalAdapter
from tests.fake_terminal import FakeTerminal

SESSION_ID = SessionId("session-one")
ACTOR_ID = ActorId("actor-one")


class FixedModels:
    def __init__(self, responses: tuple[str, ...] = (), *, unavailable: bool = False) -> None:
        self.responses = list(responses)
        self.unavailable = unavailable
        self.prompts: list[ModelPromptRequest] = []

    def big(self):
        raise NotImplementedError

    def mid(self):
        raise NotImplementedError

    def small(self):
        return self

    def send(self, model_prompt_request: ModelPromptRequest) -> ModelPromptResponse:
        self.prompts.append(model_prompt_request)
        if self.unavailable:
            raise ModelUnavailableError("unavailable")
        return ModelPromptResponse(self.responses.pop(0))


class RawEvents:
    def __init__(self) -> None:
        self.items: list[RawEvent] = []

    def record(self, raw_events) -> None:
        self.items.extend(raw_events)


class ReadModel:
    def __init__(self, prompt: str) -> None:
        self.prompt = prompt

    def entries_of_types(self, session_id, entry_types):
        del session_id, entry_types
        return (
            SessionEntry(
                CanonicalEventId("prompt-event"),
                SESSION_ID,
                ACTOR_ID,
                None,
                None,
                1.0,
                None,
                MessageBody(
                    MessageId("message-one"),
                    MessageRole.USER,
                    MessagePhase.PROMPT,
                    TextContent(self.prompt),
                ),
            ),
        )


class Audit:
    def __init__(self) -> None:
        self.states: list[object] = []
        self.errors: list[object] = []

    def state_file(self, *arguments) -> None:
        self.states.append(arguments)

    def error(self, *arguments) -> None:
        self.errors.append(arguments)


class Sessions:
    def __init__(self, stored_session: Session) -> None:
        self.stored_session = stored_session

    def find(self, session_id: SessionId) -> Session | None:
        return self.stored_session if self.stored_session.session_id == session_id else None


class Adapter:
    def window_for_session(self, session_id: SessionId) -> WindowId | None:
        del session_id
        return cast(WindowId | None, None)


class ControlReadModel:
    def read(self, session_id: SessionId) -> SessionData | None:
        del session_id
        return cast(SessionData | None, None)

    def pending_attention(self, session_id: SessionId) -> tuple[SessionEntry, ...]:
        del session_id
        return ()


class Effects:
    def __init__(self) -> None:
        self.renames: list[RenameSession] = []

    def session_renamed(self, stored_session: Session, rename_session: RenameSession) -> None:
        del stored_session
        self.renames.append(rename_session)


class RecordingNamer:
    def __init__(self) -> None:
        self.calls = 0

    def requested_name(self, stored_session, request_id, apply_title):
        del stored_session, request_id
        self.calls += 1
        return apply_title("Generated generic control title")


class AcknowledgingHandler:
    def __init__(self) -> None:
        self.requests: list[ControlRequest] = []

    def __call__(
        self,
        control_request: ControlRequest,
        control_context: ControlContext,
    ) -> ControlResult:
        del control_context
        self.requests.append(control_request)
        if isinstance(control_request, RenameSession):
            return DurableTitleResult(
                control_request.request_id,
                ControlAcknowledgement.ACKNOWLEDGED,
            )
        return ControlResult(
            control_request.request_id,
            ControlAcknowledgement.ACKNOWLEDGED,
        )


def session() -> Session:
    return Session(
        SESSION_ID,
        ACTOR_ID,
        "/tmp/rollout-session-one.jsonl",
        "/tmp",
        plugin=codex_plugin,
    )


def namer(
    model_factory: FixedModels,
    jobs: SqliteNamingJobRepository,
    raw_events: RawEvents,
    prompt: str = "Implement automatic concise naming",
) -> AutomaticSessionNamer:
    return AutomaticSessionNamer(
        cast(ModelFactory, model_factory),
        jobs,
        cast(RawEventRepository, raw_events),
        cast(SessionDataRepository, ReadModel(prompt)),
        cast(AuditRecorder, Audit()),
    )


def prompt_event(*, claude: bool = False) -> CanonicalEvent[EventPayload]:
    plugin = claude_plugin if claude else codex_plugin
    return CanonicalEvent(
        event_id=CanonicalEventId("prompt-event"),
        session_id=SESSION_ID,
        actor_id=ACTOR_ID,
        turn_id=None,
        parent_actor_id=None,
        harness=plugin.info.name,
        occurred_at=1.0,
        terminal_window_id=None,
        harness_process_id=None,
        payload=MessageCreated(
            MessageId("message-one"),
            MessageRole.USER,
            TextContent("A very long first semantic prompt"),
            MessagePhase.PROMPT,
            None,
        ),
    )


def test_title_normalization_selects_one_safe_bounded_line() -> None:
    title = normalize_title(
        "**Plan the reliable database schema migration with extra ignored words**\n"
        "https://example.invalid/output"
    )

    assert title == "Plan the reliable database schema migration with extra"
    assert len(title) <= 80
    assert len(title.split()) == 8


@pytest.mark.parametrize("title", ("", "one", "two words", "\n\t"))
def test_title_normalization_rejects_empty_or_too_short_results(title: str) -> None:
    with pytest.raises(ModelUnavailableError):
        normalize_title(title)


def test_first_prompt_enqueues_once_and_a_restart_cannot_claim_it_twice(tmp_path) -> None:
    repository = SqliteNamingJobRepository(main_database(str(tmp_path / "main.db")))
    registry = HarnessRegistry()
    registry.register(codex_plugin)
    reaction = AutomaticNamingReaction(registry, repository)

    reaction.react(prompt_event())
    reaction.react(prompt_event())
    claimed = repository.claim_next()

    assert claimed is not None
    assert claimed.key == f"initial:{SESSION_ID}"
    assert claimed.prompt == "A very long first semantic prompt"
    restarted = SqliteNamingJobRepository(main_database(str(tmp_path / "main.db")))
    assert restarted.claim_next() is None


def test_native_automatic_naming_never_enqueues_a_model_job(tmp_path) -> None:
    repository = SqliteNamingJobRepository(main_database(str(tmp_path / "main.db")))
    registry = HarnessRegistry()
    registry.register(claude_plugin)

    AutomaticNamingReaction(registry, repository).react(prompt_event(claude=True))

    assert repository.claim_next() is None


def test_installed_harnesses_validate_native_and_generic_naming_routes() -> None:
    registry = HarnessRegistry()
    registry.register(codex_plugin)
    registry.register(claude_plugin)

    registry.validate()


@pytest.mark.parametrize(
    "changed_plugin",
    (
        replace(
            codex_plugin,
            info=replace(codex_plugin.info, supports_native_automatic_renaming=True),
        ),
        replace(
            claude_plugin,
            info=replace(claude_plugin.info, supports_native_automatic_renaming=False),
        ),
    ),
)
def test_registry_rejects_a_capability_that_disagrees_with_its_handler(
    changed_plugin: HarnessPlugin,
) -> None:
    registry = HarnessRegistry()
    registry.register(changed_plugin)
    registry.register(
        claude_plugin
        if changed_plugin.info.name == HarnessName.CODEX
        else codex_plugin
    )

    with pytest.raises(HarnessRegistryError):
        registry.validate()


def test_initial_name_records_only_an_automatic_title_observation(tmp_path) -> None:
    jobs = SqliteNamingJobRepository(main_database(str(tmp_path / "main.db")))
    raw_events = RawEvents()
    service = namer(FixedModels(("Concise automatic session title",)), jobs, raw_events)

    title = service.initial_name(session(), "Implement automatic concise naming")

    assert title == "Concise automatic session title"
    assert len(raw_events.items) == 1
    translated = AutomaticTitleTranslator().translate(raw_events.items[0])
    assert translated.canonical_events[0].payload == SessionTitleChanged(
        title,
        TitleOrigin.AUTOMATIC,
    )


def test_each_explicit_request_generates_fresh_then_retries_idempotently(tmp_path) -> None:
    jobs = SqliteNamingJobRepository(main_database(str(tmp_path / "main.db")))
    raw_events = RawEvents()
    models = FixedModels(("First explicit session title", "Second explicit session title"))
    service = namer(models, jobs, raw_events)
    applied: list[str] = []

    def apply(title: str) -> DurableTitleResult:
        applied.append(title)
        return DurableTitleResult(RequestId("ignored"), ControlAcknowledgement.ACKNOWLEDGED)

    first = service.requested_name(session(), RequestId("one"), apply)
    second = service.requested_name(session(), RequestId("two"), apply)
    retried = service.requested_name(session(), RequestId("one"), apply)

    assert first.status == second.status == retried.status == ControlAcknowledgement.ACKNOWLEDGED
    assert len(models.prompts) == 2
    assert applied == [
        "First explicit session title",
        "Second explicit session title",
        "First explicit session title",
    ]
    assert "Implement automatic concise naming" in models.prompts[0].prompt


def test_explicit_failure_keeps_the_current_title_unchanged(tmp_path) -> None:
    jobs = SqliteNamingJobRepository(main_database(str(tmp_path / "main.db")))
    service = namer(FixedModels(unavailable=True), jobs, RawEvents())
    applied: list[str] = []

    def apply(title: str) -> DurableTitleResult:
        applied.append(title)
        return DurableTitleResult(RequestId("failed"), ControlAcknowledgement.ACKNOWLEDGED)

    outcome = service.requested_name(
        session(),
        RequestId("failed"),
        apply,
    )

    assert outcome.status == ControlAcknowledgement.INDETERMINATE
    assert outcome.reason == "no small model is currently available"
    assert applied == []
    stored = jobs.find(f"requested:{SESSION_ID}:failed")
    assert stored is not None and stored.state == NamingJobState.FAILED


def test_job_completion_is_durable(tmp_path) -> None:
    database = main_database(str(tmp_path / "main.db"))
    repository = SqliteNamingJobRepository(database)
    job = NamingJob("initial:one", SESSION_ID, "prompt")

    assert repository.enqueue(job)
    assert not repository.enqueue(job)
    claimed = repository.claim_next()
    assert claimed is not None and claimed.state == NamingJobState.RUNNING
    repository.complete(job.key, "Durable generated session title")

    stored = SqliteNamingJobRepository(database).find(job.key)
    assert stored is not None
    assert stored.title == "Durable generated session title"
    assert stored.state == NamingJobState.COMPLETED


def test_version_thirteen_database_gains_the_naming_queue(tmp_path) -> None:
    path = str(tmp_path / "main.db")
    old_database = main_database(path)
    old_database.initialize()
    with old_database.write() as connection:
        connection.execute("DROP TABLE naming_jobs")
        connection.execute("UPDATE schema_version SET version=13 WHERE id=1")

    upgraded = main_database(path)
    upgraded.initialize()
    with upgraded.read() as connection:
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='naming_jobs'"
        ).fetchone()
        version = connection.execute(
            "SELECT version FROM schema_version WHERE id=1"
        ).fetchone()

    assert table is not None
    assert version is not None and version["version"] == MAIN_SCHEMA_VERSION


def control_service(
    stored_session: Session,
    automatic_namer: RecordingNamer,
    effects: Effects,
) -> HarnessControlService:
    return HarnessControlService(
        cast(SessionRepository, Sessions(stored_session)),
        cast(TerminalAdapter, Adapter()),
        FakeTerminal().plugin(),
        cast(SessionDataRepository, ControlReadModel()),
        cast(AuditRecorder, Audit()),
        InterruptRegistry(),
        cast(ControlEffectRecorder, effects),
        cast(AutomaticSessionNamer, automatic_namer),
    )


def test_codex_auto_name_routes_through_generic_generation_and_existing_rename() -> None:
    handler = AcknowledgingHandler()
    plugin = replace(
        codex_plugin,
        controller=HarnessController(
            {ControlName.RENAME_SESSION: cast(ControlHandler, handler)}
        ),
    )
    stored_session = replace(session(), plugin=plugin)
    automatic_namer = RecordingNamer()
    effects = Effects()

    outcome = control_service(stored_session, automatic_namer, effects).auto_name_session(
        AutoNameSession(SESSION_ID, RequestId("generic"))
    )

    assert outcome.status == ControlAcknowledgement.ACKNOWLEDGED
    assert automatic_namer.calls == 1
    assert handler.requests == [
        RenameSession(SESSION_ID, RequestId("generic"), "Generated generic control title")
    ]
    assert effects.renames == handler.requests


def test_claude_auto_name_stays_on_its_native_handler() -> None:
    handler = AcknowledgingHandler()
    plugin = replace(
        claude_plugin,
        controller=HarnessController(
            {ControlName.AUTO_NAME_SESSION: cast(ControlHandler, handler)}
        ),
    )
    stored_session = replace(session(), plugin=plugin)
    automatic_namer = RecordingNamer()

    outcome = control_service(stored_session, automatic_namer, Effects()).auto_name_session(
        AutoNameSession(SESSION_ID, RequestId("native"))
    )

    assert outcome.status == ControlAcknowledgement.ACKNOWLEDGED
    assert automatic_namer.calls == 0
    assert handler.requests == [AutoNameSession(SESSION_ID, RequestId("native"))]


@pytest.mark.parametrize("hook", ("codex_hook.py", "claude_hook.py"))
def test_internal_model_marker_suppresses_hook_delivery(
    hook: str,
) -> None:
    environment = os.environ.copy()
    environment["BAQYLAU_INTERNAL_MODEL"] = "1"
    environment["BAQYLAU_DASHBOARD_PORT"] = "1"

    completed = subprocess.run(
        [sys.executable, Path(__file__).resolve().parents[1] / "client" / hook],
        input=b'{"session_id":"internal-model"}',
        capture_output=True,
        env=environment,
        timeout=5,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stdout == b""
