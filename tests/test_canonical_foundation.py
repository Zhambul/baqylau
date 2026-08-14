"""Contract tests for the canonical spine in the architecture proposal."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.delivery import ApplicationEventDelivery, SessionLifecycleService
from app.host import ApplicationHost
from app.observe import ObservationRunner
from app.evidence_cli import main as evidence_main
from contracts.harness import (
    HarnessInfo,
    HarnessPlugin,
    IngestionResult,
    RawEvent,
    RecognizedSession,
    SourceCheckpoint,
    TranslationError,
    TranslationResult,
)
from domain.codec import SCHEMA_VERSION, CanonicalCodecError, CanonicalEventCodec
from domain.events import (
    ActorAssignmentFinished,
    ActorAssignmentStarted,
    ActorFinished,
    ActorStarted,
    CanonicalEvent,
    MessageCreated,
    OperationInputProvided,
    SessionStarted,
)
from domain.ids import (
    ActorId,
    AssignmentId,
    CanonicalEventId,
    MessageId,
    OperationId,
    RawEventId,
    SessionId,
    stable_event_id,
)
from domain.values import StructuredContent, TextContent
from runtime.event_store import EventIdentityConflict, EventStore, EventStoreError
from runtime.database import connect
from runtime.evidence import EvidenceQueries
from runtime.ingest import EventPipeline
from runtime.registry import HarnessRegistry, HarnessRegistryError
from runtime.state import SqliteCheckpointStore
from dashboard.presenter import DashboardPresenter
from runtime.projections import ActivityScope, SessionQueries
from terminal.presenter import TerminalPresenter


class FixedSessionRecognizer:
    def __init__(self, session: RecognizedSession) -> None:
        self.session = session

    def discover(self) -> tuple[RecognizedSession, ...]:
        return (self.session,)

    def recognize(self, candidate):
        return self.session if candidate.source_reference == self.session.source_reference else None


class FixedEvents:
    def __init__(
        self,
        translation: TranslationResult | TranslationError,
        sources=(),
    ) -> None:
        self.translation = translation
        self.event_sources = sources

    def sources(self, session, checkpoints):
        return self.event_sources

    def translate(self, raw_event):
        if isinstance(self.translation, TranslationError):
            raise self.translation
        return self.translation


class FixedSource:
    def __init__(self, raw_event: RawEvent) -> None:
        self.raw_event = raw_event

    def drain(self, delivery) -> None:
        delivery.deliver(self.raw_event)


class PipelineDelivery:
    def __init__(self, pipeline: EventPipeline) -> None:
        self.pipeline = pipeline

    def deliver(self, raw_event: RawEvent):
        return self.pipeline.ingest(raw_event)


@pytest.mark.parametrize(
    ("payload", "event_type", "expected_payload"),
    (
        (
            ActorStarted("weather researcher", "child"),
            "actor.started",
            {"name": "weather researcher", "role": "child"},
        ),
        (
            ActorAssignmentStarted(
                AssignmentId("assignment-one"),
                TextContent("Get Bali weather"),
            ),
            "actor.assignment_started",
            {
                "assignment_id": "assignment-one",
                "brief": {"media_type": "text/plain", "text": "Get Bali weather"},
            },
        ),
        (
            ActorAssignmentFinished(
                AssignmentId("assignment-one"),
                "succeeded",
                TextContent("Sunny"),
                None,
            ),
            "actor.assignment_finished",
            {
                "assignment_id": "assignment-one",
                "outcome": "succeeded",
                "reason": None,
                "result": {"media_type": "text/plain", "text": "Sunny"},
            },
        ),
        (
            ActorFinished("process exited"),
            "actor.finished",
            {"reason": "process exited"},
        ),
        (
            OperationInputProvided(
                OperationId("operation-one"),
                TextContent("yes\n"),
                False,
            ),
            "operation.input_provided",
            {
                "operation_id": "operation-one",
                "content": {"media_type": "text/plain", "text": "yes\n"},
                "closed": False,
            },
        ),
    ),
)
def test_actor_lifecycle_wire_contract(payload, event_type, expected_payload):
    event = CanonicalEvent(
        event_id=CanonicalEventId("event-one"),
        session_id=SessionId("session-one"),
        actor_id=ActorId("actor-one"),
        turn_id=None,
        parent_actor_id=None,
        harness="example",
        occurred_at=1.0,
        payload=payload,
    )

    encoded = json.loads(CanonicalEventCodec().encode(event))

    assert encoded["event_type"] == event_type
    assert encoded["payload"] == expected_payload
    assert "child" not in encoded["event_type"]


def test_runtime_database_connection_is_closed(tmp_path):
    database_path = str(tmp_path / "events.db")
    with connect(database_path) as connection:
        opened_connection = connection
        connection.execute("CREATE TABLE example(value TEXT)")

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened_connection.execute("SELECT * FROM example")


def test_registered_candidate_is_not_a_canonical_session(tmp_path):
    event_store = EventStore(str(tmp_path / "events.db"))
    event_store.register_session(
        "example",
        RecognizedSession(
            SessionId("candidate"),
            ActorId("lead"),
            "native-candidate",
            "/tmp/candidate.jsonl",
            "/work",
            None,
        ),
    )

    assert event_store.session_ids() == ()


def test_application_host_starts_one_silent_singleton(monkeypatch):
    holders = iter((0, 0, 91))
    monkeypatch.setattr("app.host.cli.holder", lambda: next(holders))
    processes = []
    monkeypatch.setattr(
        "app.host.subprocess.Popen",
        lambda arguments, **options: processes.append((arguments, options)),
    )
    monkeypatch.setattr("app.host.time.sleep", lambda seconds: None)

    ApplicationHost().ensure_running()

    assert len(processes) == 1
    arguments, options = processes[0]
    assert arguments[-1] == "serve"
    assert arguments[-2].endswith("bin/baqylau-dashboard.py")
    assert options["start_new_session"] is True
    assert options["stdout"] is not None
    assert options["stderr"] is not None


def test_public_dashboard_url_is_an_allowed_post_origin():
    from dashboard import config

    assert config.PUBLIC_URL in config.ALLOWED_ORIGINS


def canonical_message(
    *,
    event_id: str = "event-message",
    session_id: str = "session-one",
    actor_id: str = "actor-lead",
    harness: str = "example",
    text: str = "hello",
) -> CanonicalEvent:
    return CanonicalEvent(
        event_id=CanonicalEventId(event_id),
        session_id=SessionId(session_id),
        actor_id=ActorId(actor_id),
        turn_id=None,
        parent_actor_id=None,
        harness=harness,
        occurred_at=10.0,
        payload=MessageCreated(
            message_id=MessageId("message-one"),
            role="user",
            content=TextContent(text),
            phase="prompt",
            reply_to=None,
        ),
    )


def raw_observation(raw_event_id: str, *, harness: str = "example", payload: bytes = b'{"kind":"message"}'):
    return RawEvent(
        raw_event_id=RawEventId(raw_event_id),
        harness=harness,
        source_type="transcript",
        source_name="fixture.jsonl",
        source_position="0",
        session_id=SessionId("session-one"),
        actor_id=ActorId("actor-lead"),
        parent_actor_id=None,
        observed_at=11.0,
        encoding="jsonl",
        payload=payload,
    )


def registered_runtime(tmp_path, translation: TranslationResult | TranslationError):
    store = EventStore(str(tmp_path / "events.db"))
    session = RecognizedSession(
        session_id=SessionId("session-one"),
        lead_actor_id=ActorId("actor-lead"),
        native_session_id="native-one",
        source_reference="fixture.jsonl",
        working_directory="/work",
    )
    plugin = HarnessPlugin(
        info=HarnessInfo("example", "Example", "1.0", SCHEMA_VERSION),
        sessions=FixedSessionRecognizer(session),
        events=FixedEvents(translation),
    )
    registry = HarnessRegistry(store)
    registry.register(plugin)
    store.register_session("example", session)
    registry.discover_sessions()
    return store, registry, EventPipeline(registry, store)


def test_registry_requires_one_explicit_default_when_launchers_exist(tmp_path):
    store = EventStore(str(tmp_path / "events.db"))
    session = RecognizedSession(
        SessionId("session-one"),
        ActorId("actor-lead"),
        "native-one",
        "fixture.jsonl",
        "/work",
    )
    plugin = HarnessPlugin(
        HarnessInfo("example", "Example", "1", SCHEMA_VERSION),
        FixedSessionRecognizer(session),
        FixedEvents(TranslationResult((), "ignored_nonsemantic")),
        launcher=object(),
    )
    registry = HarnessRegistry(store)
    registry.register(plugin)

    with pytest.raises(HarnessRegistryError, match="no launchable harness"):
        registry.validate()


def test_registry_rejects_multiple_launch_defaults(tmp_path):
    store = EventStore(str(tmp_path / "events.db"))
    session = RecognizedSession(
        SessionId("session-one"),
        ActorId("actor-lead"),
        "native-one",
        "fixture.jsonl",
        "/work",
    )
    registry = HarnessRegistry(store)
    for name in ("first", "second"):
        plugin = HarnessPlugin(
            HarnessInfo(name, name.title(), "1", SCHEMA_VERSION, default_for_launch=True),
            FixedSessionRecognizer(session),
            FixedEvents(TranslationResult((), "ignored_nonsemantic")),
            launcher=object(),
        )
        if name == "first":
            registry.register(plugin)
        else:
            with pytest.raises(HarnessRegistryError, match="multiple harnesses"):
                registry.register(plugin)


def test_codec_round_trip_is_deterministic_and_structured_content_is_canonical():
    codec = CanonicalEventCodec()
    event = canonical_message()
    assert codec.decode(codec.encode(event)) == event
    assert codec.encode(codec.decode(codec.encode(event))) == codec.encode(event)
    assert StructuredContent('{ "z": 1, "a": [true] }').json_text == '{"a":[true],"z":1}'


def test_codec_rejects_unknown_schema_and_envelope_fields():
    codec = CanonicalEventCodec()
    document = codec.encode(canonical_message()).decode()
    with pytest.raises(CanonicalCodecError, match="schema version"):
        codec.decode(document.replace(f'"schema_version":{SCHEMA_VERSION}', '"schema_version":999'))
    with pytest.raises(CanonicalCodecError, match="envelope fields"):
        codec.decode(document[:-1] + ',"glyph":"x"}')


def test_codec_rejects_an_invalid_payload_before_storage():
    codec = CanonicalEventCodec()
    event = canonical_message()
    invalid_payload = replace(event.payload, role="tool")

    with pytest.raises(CanonicalCodecError, match="invalid literal"):
        codec.encode(replace(event, payload=invalid_payload))


def test_stable_event_id_names_the_same_fact_and_distinguishes_its_phase():
    arguments = {
        "harness": "example",
        "session_id": SessionId("session-one"),
        "actor_id": ActorId("actor-lead"),
        "subject_type": "operation",
        "subject_id": "native-call",
    }
    started = stable_event_id(**arguments, phase="started")
    assert started == stable_event_id(**arguments, phase="started")
    assert started != stable_event_id(**arguments, phase="finished")
    assert started != stable_event_id(
        **(arguments | {"actor_id": ActorId("actor-child")}),
        phase="started",
    )


def test_application_delivery_applies_lifecycle_only_after_ingestion():
    event = CanonicalEvent(
        CanonicalEventId("session-started"),
        SessionId("session-one"),
        ActorId("actor-lead"),
        None,
        None,
        "example",
        10.0,
        SessionStarted("/work", None, None, None, None, None),
    )
    calls = []

    class Pipeline:
        def ingest(self, raw_event):
            calls.append(("ingest", raw_event.raw_event_id))
            return IngestionResult(
                raw_event.raw_event_id,
                "translated",
                (event.event_id,),
                (),
                1,
            )

    class Store:
        def require_event(self, event_id):
            calls.append(("read", event_id))
            return SimpleNamespace(event=event)

    class Lifecycle:
        def apply(self, applied_event):
            calls.append(("lifecycle", applied_event.event_id))

    ApplicationEventDelivery(Pipeline(), Store(), Lifecycle()).deliver(
        raw_observation("raw-session-started")
    )

    assert calls == [
        ("ingest", RawEventId("raw-session-started")),
        ("read", event.event_id),
        ("lifecycle", event.event_id),
    ]


def test_application_delivery_does_not_repeat_lifecycle_for_deduplicated_facts():
    class Pipeline:
        def ingest(self, raw_event):
            return IngestionResult(
                raw_event.raw_event_id,
                "translated",
                (),
                (CanonicalEventId("already-stored"),),
                1,
            )

    class Store:
        def require_event(self, event_id):
            raise AssertionError(f"deduplicated event was read: {event_id}")

    class Lifecycle:
        def apply(self, event):
            raise AssertionError(f"deduplicated lifecycle was repeated: {event}")

    ApplicationEventDelivery(Pipeline(), Store(), Lifecycle()).deliver(
        raw_observation("raw-retry")
    )


def test_raw_event_replay_does_not_repeat_committed_lifecycle(tmp_path):
    event = CanonicalEvent(
        CanonicalEventId("session-started"),
        SessionId("session-one"),
        ActorId("actor-lead"),
        None,
        None,
        "example",
        10.0,
        SessionStarted("/work", None, None, None, None, None),
    )
    store, _registry, pipeline = registered_runtime(
        tmp_path,
        TranslationResult((event,), "translated"),
    )
    applied = []

    class Lifecycle:
        def apply(self, applied_event):
            applied.append(applied_event.event_id)

    delivery = ApplicationEventDelivery(pipeline, store, Lifecycle())
    raw_event = raw_observation("raw-session-started")
    delivery.deliver(raw_event)
    delivery.deliver(replace(raw_event, observed_at=99.0))

    assert applied == [event.event_id]


def test_session_lifecycle_service_routes_only_session_facts():
    actions = []

    class Lifecycle:
        def apply(self, request, session, context):
            actions.append((request.action, session.session_id, context))

    session = RecognizedSession(
        SessionId("session-one"),
        ActorId("actor-lead"),
        "native-one",
        "fixture.jsonl",
        "/work",
    )
    context = object()
    registered = SimpleNamespace(
        plugin=SimpleNamespace(lifecycle=Lifecycle()),
        session=session,
    )
    registry = SimpleNamespace(registered_session=lambda session_id: registered)
    service = SessionLifecycleService(registry, context)
    started = CanonicalEvent(
        CanonicalEventId("session-started"),
        session.session_id,
        session.lead_actor_id,
        None,
        None,
        "example",
        10.0,
        SessionStarted("/work", None, None, None, None, None),
    )

    service.apply(started)
    service.apply(canonical_message())

    assert actions == [("started", session.session_id, context)]


def test_ingestion_commits_raw_translation_canonical_and_provenance_together(tmp_path):
    event = canonical_message()
    translation = TranslationResult((event,), "translated")
    store, _registry, pipeline = registered_runtime(tmp_path, translation)

    result = pipeline.ingest(raw_observation("raw-one"))
    assert result.accepted_event_ids == (event.event_id,)
    assert result.deduplicated_event_ids == ()
    assert store.after(SessionId("session-one"), 0, 10).events[0].event == event

    connection = sqlite3.connect(store.database_path)
    assert connection.execute("SELECT count(*) FROM raw_events").fetchone()[0] == 1
    assert connection.execute("SELECT decision FROM translation_records").fetchone()[0] == "translated"
    assert connection.execute(
        "SELECT event_order, storage_result FROM canonical_provenance"
    ).fetchone() == (0, "accepted")


def test_replay_is_idempotent_and_a_second_observation_adds_provenance(tmp_path):
    event = canonical_message()
    translation = TranslationResult((event,), "translated")
    store, _registry, pipeline = registered_runtime(tmp_path, translation)

    first = pipeline.ingest(raw_observation("raw-one"))
    retried = replace(raw_observation("raw-one"), observed_at=99.0)
    replay = pipeline.ingest(retried)
    second = pipeline.ingest(raw_observation("raw-two"))

    assert replay.accepted_event_ids == ()
    assert replay.deduplicated_event_ids == (event.event_id,)
    assert replay.latest_cursor == first.latest_cursor
    assert second.accepted_event_ids == ()
    assert second.deduplicated_event_ids == (event.event_id,)
    stored = store.after(SessionId("session-one"), 0, 10).events
    assert len(stored) == 1
    assert stored[0].raw_event_ids == (RawEventId("raw-one"), RawEventId("raw-two"))


def test_reused_raw_identity_rolls_back_the_complete_observation(tmp_path):
    event = canonical_message()
    _store, _registry, pipeline = registered_runtime(tmp_path, TranslationResult((event,), "translated"))
    pipeline.ingest(raw_observation("raw-one"))

    with pytest.raises(EventIdentityConflict, match="raw event identity reused"):
        pipeline.ingest(raw_observation("raw-one", payload=b"different"))


def test_re_observing_one_fact_is_idempotent_even_when_observers_disagree(tmp_path):
    """A canonical identity names a FACT, so re-observing it only adds provenance.

    Several sources legitimately converge on one event (a hook, the transcript, the
    foreground tee) and may render it differently. The first writer stays authoritative
    and the later rendering stays recoverable from its own raw evidence. Raising here
    instead aborted the whole observation pass and killed the scheduler thread.
    """
    store = EventStore(str(tmp_path / "events.db"))
    store.register_session(
        "example",
        RecognizedSession(
            session_id=SessionId("session-one"),
            lead_actor_id=ActorId("actor-lead"),
            native_session_id="native-one",
            source_reference="fixture.jsonl",
            working_directory="/work",
        ),
    )
    store.record(raw_observation("raw-one"), "1.0", TranslationResult((canonical_message(),), "translated"))

    result = store.record(
        raw_observation("raw-two"),
        "1.0",
        TranslationResult((canonical_message(text="changed"),), "translated"),
    )

    assert result.accepted == ()
    assert result.duplicate_event_ids == (CanonicalEventId("event-message"),)
    stored = store.after(SessionId("session-one"), 0, 10).events
    assert len(stored) == 1
    assert stored[0].event.payload.content.text == "hello"
    assert stored[0].raw_event_ids == (RawEventId("raw-one"), RawEventId("raw-two"))
    # Nothing is lost: the disagreeing rendering survives as its own raw evidence.
    connection = sqlite3.connect(store.database_path)
    assert connection.execute(
        "SELECT count(*) FROM raw_events WHERE raw_event_id='raw-two'"
    ).fetchone()[0] == 1


def test_translation_cannot_move_raw_evidence_to_another_actor(tmp_path):
    event = canonical_message(actor_id="actor-child")
    store, _registry, pipeline = registered_runtime(
        tmp_path,
        TranslationResult((event,), "translated"),
    )

    with pytest.raises(EventStoreError, match="actor does not match"):
        pipeline.ingest(raw_observation("raw-child"))

    connection = sqlite3.connect(store.database_path)
    assert connection.execute(
        "SELECT count(*) FROM raw_events WHERE raw_event_id='raw-child'"
    ).fetchone()[0] == 0


def test_translation_failure_is_a_complete_audited_decision(tmp_path):
    store, _registry, pipeline = registered_runtime(
        tmp_path,
        TranslationError("malformed record", context="line 1"),
    )
    result = pipeline.ingest(raw_observation("raw-bad", payload=b"not json"))
    assert result.translation_decision == "translation_failed"
    connection = sqlite3.connect(store.database_path)
    assert connection.execute(
        "SELECT decision, reason FROM translation_records WHERE raw_event_id='raw-bad'"
    ).fetchone() == ("translation_failed", "malformed record: line 1")
    assert connection.execute("SELECT count(*) FROM canonical_events").fetchone()[0] == 0


def test_evidence_cli_prints_exact_raw_and_canonical_correlation(tmp_path, monkeypatch, capsys):
    data_directory = tmp_path / "data"
    store = EventStore(str(data_directory / "events.db"))
    session = RecognizedSession(
        SessionId("session-one"),
        ActorId("actor-lead"),
        "native-one",
        "fixture",
        "/work",
    )
    store.register_session("example", session)
    raw_event = raw_observation("raw-one", payload=b"exact bytes\n")
    store.record(raw_event, "1", TranslationResult((canonical_message(),), "translated"))
    monkeypatch.setenv("BAQYLAU_DATA_DIR", str(data_directory))

    assert evidence_main(["raw", "raw-one"]) == 0

    document = json.loads(capsys.readouterr().out)
    assert document["payload_base64"] == "ZXhhY3QgYnl0ZXMK"
    assert document["canonical"][0]["event"]["event_id"] == "event-message"


def test_actor_start_persists_mixed_harness_ownership(tmp_path):
    store = EventStore(str(tmp_path / "events.db"))
    session = RecognizedSession(
        SessionId("session-one"),
        ActorId("actor-lead"),
        "native-one",
        "fixture",
        "/work",
    )
    store.register_session("lead_harness", session)
    event = CanonicalEvent(
        event_id=CanonicalEventId("actor-start"),
        session_id=session.session_id,
        actor_id=ActorId("actor-child"),
        turn_id=None,
        parent_actor_id=ActorId("actor-lead"),
        harness="child_harness",
        occurred_at=10.0,
        payload=ActorStarted("worker", "child"),
    )
    store.record(
        replace(
            raw_observation("raw-actor", harness="child_harness"),
            actor_id=ActorId("actor-child"),
            parent_actor_id=ActorId("actor-lead"),
        ),
        "1.0",
        TranslationResult((event,), "translated"),
    )
    assert store.actor_harness(session.session_id, ActorId("actor-child")) == "child_harness"


def test_recognition_registers_lead_actor_ownership_immediately(tmp_path):
    store = EventStore(str(tmp_path / "events.db"))
    session = RecognizedSession(
        SessionId("session-one"),
        ActorId("actor-lead"),
        "native-one",
        "fixture",
        "/work",
    )

    store.register_session("lead_harness", session)

    assert store.actor_harness(session.session_id, session.lead_actor_id) == "lead_harness"


def test_session_source_metadata_can_move_without_changing_ownership(tmp_path):
    store = EventStore(str(tmp_path / "events.db"))
    session = RecognizedSession(
        SessionId("session-one"),
        ActorId("actor-lead"),
        "native-one",
        "first.jsonl",
        "/first",
    )
    store.register_session("example", session)

    store.register_session(
        "example",
        replace(session, source_reference="second.jsonl", working_directory="/second"),
    )

    connection = sqlite3.connect(store.database_path)
    assert connection.execute(
        "SELECT source_reference, working_directory FROM session_harness"
    ).fetchone() == ("second.jsonl", "/second")


def test_source_checkpoints_use_descriptive_opaque_values(tmp_path):
    store = EventStore(str(tmp_path / "events.db"))
    session = RecognizedSession(
        SessionId("session-one"),
        ActorId("lead-one"),
        "native-one",
        "fixture.jsonl",
        "/work",
    )
    store.register_session("example", session)
    checkpoints = SqliteCheckpointStore(store)
    assert checkpoints.load("rollout-file") is None
    checkpoints.commit(SourceCheckpoint(session.session_id, "rollout-file", "byte:42"))
    assert checkpoints.load("rollout-file") == SourceCheckpoint(
        session.session_id,
        "rollout-file",
        "byte:42",
    )

    store.delete_session(session.session_id)

    assert checkpoints.load("rollout-file") is None


def test_evidence_queries_show_exact_raw_translation_and_canonical_chain(tmp_path):
    event = canonical_message()
    store, _registry, pipeline = registered_runtime(tmp_path, TranslationResult((event,), "translated"))
    raw = raw_observation("raw-one")
    pipeline.ingest(raw)
    evidence = EvidenceQueries(store).raw_event(raw.raw_event_id)
    assert evidence is not None
    assert evidence.payload == raw.payload
    assert evidence.decision == "translated"
    assert evidence.canonical[0].event.event_id == event.event_id
    assert evidence.canonical[0].event.actor_id == event.actor_id
    assert evidence.canonical[0].accepted_at > raw.observed_at
    assert evidence.completed_at == evidence.canonical[0].accepted_at
    assert evidence.canonical[0].storage_result == "accepted"
    assert EvidenceQueries(store).session(SessionId("session-one")) == (evidence,)


def test_deleting_a_session_deletes_its_complete_evidence_chain(tmp_path):
    event = canonical_message()
    store, _registry, pipeline = registered_runtime(tmp_path, TranslationResult((event,), "translated"))
    pipeline.ingest(raw_observation("raw-one"))

    store.delete_session(SessionId("session-one"))

    connection = sqlite3.connect(store.database_path)
    for table in (
        "session_harness",
        "actor_harness",
        "raw_events",
        "translation_records",
        "canonical_events",
        "canonical_provenance",
    ):
        assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_synthetic_harness_uses_existing_observation_and_presentation_abstractions(tmp_path):
    event = canonical_message()
    raw_event = raw_observation("synthetic-raw")
    store = EventStore(str(tmp_path / "events.db"))
    session = RecognizedSession(
        SessionId("session-one"),
        ActorId("actor-lead"),
        "native-one",
        "fixture.jsonl",
        "/work",
    )
    plugin = HarnessPlugin(
        HarnessInfo("example", "Example", "1.0", SCHEMA_VERSION),
        FixedSessionRecognizer(session),
        FixedEvents(TranslationResult((event,), "translated"), (FixedSource(raw_event),)),
    )
    registry = HarnessRegistry(store)
    registry.register(plugin)
    store.register_session("example", session)
    pipeline = EventPipeline(registry, store)
    observer = ObservationRunner(
        registry,
        SqliteCheckpointStore(store),
        PipelineDelivery(pipeline),
    )

    observer.run_once()

    activity = SessionQueries(store).activity_after(
        SessionId("session-one"),
        0,
        ActivityScope(),
        10,
    ).activities[0]
    assert DashboardPresenter().present(activity).item_id == "message:actor-lead:message-one"
    terminal_update = TerminalPresenter().present(activity)
    assert terminal_update.updated_blocks[0].block_id == "message:actor-lead:message-one"


def test_one_failing_source_neither_stops_its_siblings_nor_the_scheduler(tmp_path, monkeypatch):
    """The scheduler drives every tailed source and nothing restarts it.

    An unguarded exception here once killed observation for EVERY session silently: the
    conversation stopped arriving while hooks (separate processes) kept flowing, so the
    session still looked alive.
    """
    audited = []
    monkeypatch.setattr(
        "app.observe._audit_failure",
        lambda where, context: audited.append((where, context)),
    )

    class BrokenSource:
        source_identity = "broken"

        def drain(self, delivery):
            raise RuntimeError("this source is broken")

    store = EventStore(str(tmp_path / "events.db"))
    session = RecognizedSession(
        SessionId("session-one"),
        ActorId("actor-lead"),
        "native-one",
        "fixture.jsonl",
        "/work",
    )
    event = canonical_message()
    raw_event = raw_observation("raw-one")
    plugin = HarnessPlugin(
        HarnessInfo("example", "Example", "1.0", SCHEMA_VERSION),
        FixedSessionRecognizer(session),
        FixedEvents(
            TranslationResult((event,), "translated"),
            (BrokenSource(), FixedSource(raw_event)),
        ),
    )
    registry = HarnessRegistry(store)
    registry.register(plugin)
    store.register_session("example", session)
    observer = ObservationRunner(
        registry,
        SqliteCheckpointStore(store),
        PipelineDelivery(EventPipeline(registry, store)),
    )

    observer.run_once()

    # The healthy sibling still drained, behind the broken one.
    assert len(store.after(SessionId("session-one"), 0, 10).events) == 1
    assert [where for where, _ in audited] == ["source drain"]
    assert audited[0][1]["source_identity"] == "broken"


def test_observer_asks_plugins_for_explicitly_related_actor_sources(tmp_path):
    store = EventStore(str(tmp_path / "events.db"))
    session = RecognizedSession(
        SessionId("session-one"),
        ActorId("actor-lead"),
        "native-one",
        "fixture.jsonl",
        "/work",
    )
    calls = []

    class EmptyRecognizer:
        def discover(self):
            return ()

        def recognize(self, candidate):
            return None

    class RecordingEvents(FixedEvents):
        def __init__(self, owner):
            super().__init__(TranslationResult((), "ignored_nonsemantic"))
            self.owner = owner

        def sources(self, recognized_session, checkpoints):
            del checkpoints
            calls.append((self.owner, recognized_session.session_id))
            return ()

    registry = HarnessRegistry(store)
    registry.register(
        HarnessPlugin(
            HarnessInfo("owner", "Owner", "1", SCHEMA_VERSION),
            FixedSessionRecognizer(session),
            RecordingEvents("owner"),
        )
    )
    registry.register(
        HarnessPlugin(
            HarnessInfo("other", "Other", "1", SCHEMA_VERSION),
            EmptyRecognizer(),
            RecordingEvents("other"),
        )
    )
    store.register_session("owner", session)

    ObservationRunner(registry, SqliteCheckpointStore(store), object()).run_once()

    assert calls == [
        ("other", SessionId("session-one")),
        ("owner", SessionId("session-one")),
    ]


def test_observation_runner_has_no_consumer_triggered_session_drain():
    assert not hasattr(ObservationRunner, "drain_session")


def test_observation_runner_reads_registered_sessions_not_historical_discovery():
    class Registry:
        def recently_observed_sessions(self, limit):
            assert limit == 4
            return ()

        def session_is_finished(self, session_id):
            raise AssertionError(f"no session should be scheduled: {session_id}")

        def discover_sessions(self, limit):
            raise AssertionError("historical discovery must not run in the observer")

    ObservationRunner(Registry(), object(), object()).run_once()


def test_observation_runner_retains_an_active_session_between_recent_batches():
    drain_count = 0
    session = SimpleNamespace(session_id=SessionId("active-session"))

    class Source:
        def drain(self, delivery):
            nonlocal drain_count
            del delivery
            drain_count += 1

    plugin = SimpleNamespace(
        events=SimpleNamespace(sources=lambda recognized, checkpoints: (Source(),))
    )
    registered = SimpleNamespace(session=session)

    class Registry:
        recent_call_count = 0

        def recently_observed_sessions(self, limit):
            del limit
            self.recent_call_count += 1
            return (registered,) if self.recent_call_count == 1 else ()

        def session_is_finished(self, session_id):
            assert session_id == SessionId("active-session")
            return False

        def plugins(self):
            return (plugin,)

    observer = ObservationRunner(Registry(), object(), object())
    observer.run_once()
    observer.run_once()

    assert drain_count == 2
