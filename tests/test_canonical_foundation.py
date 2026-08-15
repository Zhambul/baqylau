"""Contract tests for the canonical spine: record, register, interpret."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace

import pytest

from app.host import ApplicationHost
from app.interpreter import Interpreter
from app.evidence_cli import main as evidence_main
from contracts.harness import (
    FileWatch,
    HarnessInfo,
    HarnessPlugin,
    RawEvent,
    RawEventSourceContext,
    Session,
    TranslationError,
    TranslationResult,
    watch_finish_raw_event,
    watch_start_raw_event,
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
    SessionFinished,
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
from runtime.canonical_store import CanonicalEventStore
from runtime.database import connect
from runtime.evidence import EvidenceQueries
from runtime.harnesses import HarnessRegistry, HarnessRegistryError
from runtime.recorder import EventIdentityConflict, RawEventRecorder
from runtime.sessions import SessionRegistry, SessionRegistryError, UnknownSession
from runtime.watches import WatchRegistry
from dashboard.presenter import DashboardPresenter
from runtime.projections import ActivityScope, SessionQueries
from terminal.presenter import TerminalPresenter


class FixedTranslator:
    def __init__(self, translation: TranslationResult | TranslationError) -> None:
        self.translation = translation

    def translate(self, raw_event):
        if isinstance(self.translation, TranslationError):
            raise self.translation
        return self.translation


class FixedSources:
    def __init__(self, sources=()) -> None:
        self.fixed = sources

    def for_session(self, session):
        return self.fixed


class FixedReadSource:
    """Emits its raw events once; the recorded position latches it shut."""

    def __init__(self, raw_events: tuple[RawEvent, ...], identity: str = "fixture:source") -> None:
        self.raw_events = raw_events
        self.source_identity = identity

    def read(self, after_position):
        if after_position is not None:
            return ()
        return self.raw_events


class NullTerminal:
    def close_session_panes(self, session_id):
        return None

    def session_panes_are_open(self, session_id):
        return True

    def current_window(self):
        return None

    def window_for_session(self, session_id):
        return None


class NullControls:
    def execute(self, request):
        raise AssertionError(f"unexpected control: {request}")


def example_session(session_id: str = "session-one") -> Session:
    return Session(
        session_id=SessionId(session_id),
        lead_actor_id=ActorId("actor-lead"),
        native_session_id=f"native-{session_id}",
        source_reference="fixture.jsonl",
        working_directory="/work",
    )


def example_plugin(
    translation: TranslationResult | TranslationError,
    sources=(),
    name: str = "example",
) -> HarnessPlugin:
    return HarnessPlugin(
        info=HarnessInfo(name, name.title(), "1.0", SCHEMA_VERSION),
        sources=FixedSources(sources),
        translator=FixedTranslator(translation),
    )


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
        source_type="pulled",
        source_name="fixture.jsonl",
        source_position="0",
        session_id=SessionId("session-one"),
        actor_id=ActorId("actor-lead"),
        parent_actor_id=None,
        observed_at=11.0,
        encoding="jsonl",
        payload=payload,
        source_identity="fixture:source",
    )


def registered_runtime(tmp_path, translation: TranslationResult | TranslationError, sources=()):
    database_path = str(tmp_path / "events.db")
    harnesses = HarnessRegistry()
    harnesses.register(example_plugin(translation, sources))
    sessions = SessionRegistry(database_path, harnesses)
    sessions.register("example", example_session())
    recorder = RawEventRecorder(database_path)
    store = CanonicalEventStore(database_path)
    watches = WatchRegistry(database_path)
    interpreter = Interpreter(
        sessions, harnesses, recorder, watches, store, NullControls(), NullTerminal()
    )
    return store, recorder, sessions, interpreter


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


def test_a_registered_session_is_not_yet_a_canonical_session(tmp_path):
    sessions = SessionRegistry(str(tmp_path / "events.db"))
    sessions.register("example", example_session("candidate"))

    assert CanonicalEventStore(str(tmp_path / "events.db")).session_ids() == ()


def test_session_registration_is_insert_once(tmp_path):
    """The row is the FIRST observation of the session and is immutable.

    Everything that changes over a session's life (working directory, title,
    model) is a canonical fact; a second registration is a wrapper bug.
    """
    sessions = SessionRegistry(str(tmp_path / "events.db"))
    sessions.register("example", example_session())

    with pytest.raises(SessionRegistryError, match="already registered"):
        sessions.register("example", replace(example_session(), working_directory="/elsewhere"))

    loaded = sessions.find(SessionId("session-one"))
    assert loaded is not None and loaded.working_directory == "/work"


def test_sessions_carry_their_plugin_only_when_harnesses_are_attached(tmp_path):
    database_path = str(tmp_path / "events.db")
    harnesses = HarnessRegistry()
    plugin = example_plugin(TranslationResult((), "ignored_nonsemantic"))
    harnesses.register(plugin)
    SessionRegistry(database_path).register("example", example_session())

    recorder_side = SessionRegistry(database_path).load(SessionId("session-one"))
    server_side = SessionRegistry(database_path, harnesses).load(SessionId("session-one"))

    assert recorder_side.plugin is None
    assert server_side.plugin is plugin
    assert recorder_side == server_side  # attachment, not identity
    with pytest.raises(UnknownSession):
        SessionRegistry(database_path).load(SessionId("missing"))


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


def test_harness_registry_requires_one_explicit_default_when_launchers_exist():
    registry = HarnessRegistry()
    registry.register(
        replace(
            example_plugin(TranslationResult((), "ignored_nonsemantic")),
            launcher=object(),
        )
    )

    with pytest.raises(HarnessRegistryError, match="no launchable harness"):
        registry.validate()


def test_harness_registry_rejects_multiple_launch_defaults():
    registry = HarnessRegistry()
    for name in ("first", "second"):
        plugin = replace(
            example_plugin(TranslationResult((), "ignored_nonsemantic"), name=name),
            info=HarnessInfo(name, name.title(), "1", SCHEMA_VERSION, default_for_launch=True),
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


def test_evidence_for_an_unregistered_session_waits_in_the_backlog(tmp_path):
    """A hook may beat the wrapper's registration; nothing is lost or interpreted early."""
    database_path = str(tmp_path / "events.db")
    recorder = RawEventRecorder(database_path)
    store = CanonicalEventStore(database_path)
    recorder.record((raw_observation("raw-early"),))

    assert store.untranslated_raw_events(10) == ()

    SessionRegistry(database_path).register("example", example_session())
    backlog = store.untranslated_raw_events(10)
    assert [raw.raw_event_id for raw in backlog] == [RawEventId("raw-early")]


class FixedSessionEvidence:
    def __init__(self, session):
        self.session = session

    def from_raw_event(self, raw_event):
        return self.session


def test_the_interpreter_registers_a_session_from_its_own_orphan_evidence(tmp_path):
    """The wrapper registers at launch; every other launch path is announced by
    the evidence itself and registered by the interpreter."""
    database_path = str(tmp_path / "events.db")
    harnesses = HarnessRegistry()
    plugin = replace(
        example_plugin(TranslationResult((canonical_message(),), "translated")),
        session_evidence=FixedSessionEvidence(example_session()),
    )
    harnesses.register(plugin)
    sessions = SessionRegistry(database_path, harnesses)
    recorder = RawEventRecorder(database_path)
    store = CanonicalEventStore(database_path)
    interpreter = Interpreter(
        sessions, harnesses, recorder, WatchRegistry(database_path),
        store, NullControls(), NullTerminal(),
    )
    recorder.record((raw_observation("raw-orphan"),))
    assert sessions.find(SessionId("session-one")) is None

    interpreter.tick()

    registered = sessions.find(SessionId("session-one"))
    assert registered is not None and registered.plugin is plugin
    # the waiting evidence interpreted in the same tick
    assert store.untranslated_raw_events(10) == ()
    assert len(store.after(SessionId("session-one"), 0, 10).events) == 1


def test_orphan_evidence_stays_waiting_when_the_harness_cannot_name_a_session(tmp_path):
    database_path = str(tmp_path / "events.db")
    harnesses = HarnessRegistry()
    harnesses.register(example_plugin(TranslationResult((), "ignored_nonsemantic")))
    sessions = SessionRegistry(database_path, harnesses)
    recorder = RawEventRecorder(database_path)
    store = CanonicalEventStore(database_path)
    interpreter = Interpreter(
        sessions, harnesses, recorder, WatchRegistry(database_path),
        store, NullControls(), NullTerminal(),
    )
    recorder.record((raw_observation("raw-orphan"),))

    interpreter.tick()

    assert sessions.find(SessionId("session-one")) is None
    assert store.untranslated_raw_events(10) == ()  # still gated on registration
    assert [raw.raw_event_id for raw in store.unregistered_raw_events(10)] == [
        RawEventId("raw-orphan")
    ]


def test_session_evidence_naming_a_different_session_is_refused_and_audited(tmp_path, monkeypatch):
    audited = []
    monkeypatch.setattr(
        "app.interpreter._audit_failure",
        lambda where, context: audited.append((where, context)),
    )
    database_path = str(tmp_path / "events.db")
    harnesses = HarnessRegistry()
    harnesses.register(replace(
        example_plugin(TranslationResult((), "ignored_nonsemantic")),
        session_evidence=FixedSessionEvidence(example_session("some-other-session")),
    ))
    sessions = SessionRegistry(database_path, harnesses)
    recorder = RawEventRecorder(database_path)
    interpreter = Interpreter(
        sessions, harnesses, recorder, WatchRegistry(database_path),
        CanonicalEventStore(database_path), NullControls(), NullTerminal(),
    )
    recorder.record((raw_observation("raw-orphan"),))

    interpreter.tick()

    assert sessions.find(SessionId("some-other-session")) is None
    assert sessions.find(SessionId("session-one")) is None
    assert [where for where, _ in audited] == ["session evidence"]


def test_interpretation_commits_verdict_canonical_and_provenance_together(tmp_path):
    event = canonical_message()
    store, recorder, _sessions, interpreter = registered_runtime(
        tmp_path, TranslationResult((event,), "translated")
    )
    recorder.record((raw_observation("raw-one"),))

    interpreter.tick()

    assert store.untranslated_raw_events(10) == ()
    assert store.after(SessionId("session-one"), 0, 10).events[0].event == event
    connection = sqlite3.connect(store.database_path)
    assert connection.execute("SELECT count(*) FROM raw_events").fetchone()[0] == 1
    assert connection.execute("SELECT decision FROM translation_records").fetchone()[0] == "translated"
    assert connection.execute(
        "SELECT event_order, storage_result FROM canonical_provenance"
    ).fetchone() == (0, "accepted")


def test_replay_is_idempotent_and_a_second_observation_adds_provenance(tmp_path):
    event = canonical_message()
    store, recorder, _sessions, interpreter = registered_runtime(
        tmp_path, TranslationResult((event,), "translated")
    )
    recorder.record((raw_observation("raw-one"),))
    interpreter.tick()
    # An identical re-record is a no-op; a second observation converges.
    recorder.record((replace(raw_observation("raw-one"), observed_at=99.0),))
    recorder.record((raw_observation("raw-two"),))
    interpreter.tick()

    stored = store.after(SessionId("session-one"), 0, 10).events
    assert len(stored) == 1
    assert stored[0].raw_event_ids == (RawEventId("raw-one"), RawEventId("raw-two"))
    connection = sqlite3.connect(store.database_path)
    assert connection.execute(
        "SELECT storage_result FROM canonical_provenance WHERE raw_event_id='raw-two'"
    ).fetchone()[0] == "deduplicated"


def test_reused_raw_identity_is_corruption_not_convergence(tmp_path):
    recorder = RawEventRecorder(str(tmp_path / "events.db"))
    recorder.record((raw_observation("raw-one"),))

    with pytest.raises(EventIdentityConflict, match="raw event identity reused"):
        recorder.record((raw_observation("raw-one", payload=b"different"),))


def test_re_observing_one_fact_is_idempotent_even_when_observers_disagree(tmp_path):
    """A canonical identity names a FACT, so re-observing it only adds provenance.

    Several sources legitimately converge on one event (a hook, the transcript, the
    foreground tee) and may render it differently. The first writer stays authoritative
    and the later rendering stays recoverable from its own raw evidence.
    """
    store, recorder, _sessions, _interpreter = registered_runtime(
        tmp_path, TranslationResult((), "ignored_nonsemantic")
    )
    recorder.record((raw_observation("raw-one"), raw_observation("raw-two")))
    store.store_translation(
        raw_observation("raw-one"), "1.0", TranslationResult((canonical_message(),), "translated")
    )
    store.store_translation(
        raw_observation("raw-two"),
        "1.0",
        TranslationResult((canonical_message(text="changed"),), "translated"),
    )

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
    """A translator that rewrites provenance fields gets a failure verdict, not a wedge."""
    event = canonical_message(actor_id="actor-child")
    store, recorder, _sessions, interpreter = registered_runtime(
        tmp_path, TranslationResult((event,), "translated")
    )
    recorder.record((raw_observation("raw-child"),))

    interpreter.tick()

    connection = sqlite3.connect(store.database_path)
    assert connection.execute("SELECT count(*) FROM canonical_events").fetchone()[0] == 0
    decision, reason = connection.execute(
        "SELECT decision, reason FROM translation_records WHERE raw_event_id='raw-child'"
    ).fetchone()
    assert decision == "translation_failed"
    assert "actor does not match" in reason


def test_translation_failure_is_a_complete_audited_decision(tmp_path):
    store, recorder, _sessions, interpreter = registered_runtime(
        tmp_path, TranslationError("malformed record", context="line 1")
    )
    recorder.record((raw_observation("raw-bad", payload=b"not json"),))

    interpreter.tick()

    connection = sqlite3.connect(store.database_path)
    assert connection.execute(
        "SELECT decision, reason FROM translation_records WHERE raw_event_id='raw-bad'"
    ).fetchone() == ("translation_failed", "malformed record: line 1")
    assert connection.execute("SELECT count(*) FROM canonical_events").fetchone()[0] == 0


def test_a_translator_bug_becomes_a_verdict_and_never_wedges_the_backlog(tmp_path):
    """The backlog is ordered; an unverdicted row would block everything behind it."""

    class BuggyTranslator:
        def translate(self, raw_event):
            raise ZeroDivisionError("translator bug")

    database_path = str(tmp_path / "events.db")
    harnesses = HarnessRegistry()
    harnesses.register(
        HarnessPlugin(
            info=HarnessInfo("example", "Example", "1.0", SCHEMA_VERSION),
            sources=FixedSources(),
            translator=BuggyTranslator(),
        )
    )
    sessions = SessionRegistry(database_path, harnesses)
    sessions.register("example", example_session())
    recorder = RawEventRecorder(database_path)
    store = CanonicalEventStore(database_path)
    interpreter = Interpreter(
        sessions, harnesses, recorder, WatchRegistry(database_path), store, NullControls(), NullTerminal()
    )
    recorder.record((raw_observation("raw-bug"),))

    interpreter.tick()

    connection = sqlite3.connect(store.database_path)
    decision, reason = connection.execute(
        "SELECT decision, reason FROM translation_records WHERE raw_event_id='raw-bug'"
    ).fetchone()
    assert decision == "translation_failed"
    assert "ZeroDivisionError" in reason
    assert store.untranslated_raw_events(10) == ()


def test_evidence_cli_prints_exact_raw_and_canonical_correlation(tmp_path, monkeypatch, capsys):
    data_directory = tmp_path / "data"
    database_path = str(data_directory / "events.db")
    SessionRegistry(database_path).register("example", example_session())
    recorder = RawEventRecorder(database_path)
    store = CanonicalEventStore(database_path)
    raw_event = raw_observation("raw-one", payload=b"exact bytes\n")
    recorder.record((raw_event,))
    store.store_translation(raw_event, "1", TranslationResult((canonical_message(),), "translated"))
    monkeypatch.setenv("BAQYLAU_DATA_DIR", str(data_directory))

    assert evidence_main(["raw", "raw-one"]) == 0

    document = json.loads(capsys.readouterr().out)
    assert document["payload_base64"] == "ZXhhY3QgYnl0ZXMK"
    assert document["canonical"][0]["event"]["event_id"] == "event-message"


def test_a_pulled_source_resumes_from_its_last_recorded_raw_event(tmp_path):
    """Progress is derived from the evidence itself, so it can never drift."""
    recorder = RawEventRecorder(str(tmp_path / "events.db"))
    assert recorder.position("fixture:source") is None

    recorder.record((
        raw_observation("raw-one"),
        replace(raw_observation("raw-two"), source_position="42"),
    ))

    assert recorder.position("fixture:source") == "42"
    assert recorder.position("someone:else") is None


def test_evidence_queries_show_exact_raw_translation_and_canonical_chain(tmp_path):
    event = canonical_message()
    store, recorder, _sessions, interpreter = registered_runtime(
        tmp_path, TranslationResult((event,), "translated")
    )
    raw = raw_observation("raw-one")
    recorder.record((raw,))
    interpreter.tick()

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


def test_evidence_queries_show_the_untranslated_backlog(tmp_path):
    recorder = RawEventRecorder(str(tmp_path / "events.db"))
    store = CanonicalEventStore(str(tmp_path / "events.db"))
    recorder.record((raw_observation("raw-waiting"),))

    evidence = EvidenceQueries(store).raw_event(RawEventId("raw-waiting"))

    assert evidence is not None
    assert evidence.decision == "untranslated"
    assert evidence.canonical == ()


def test_the_interpreter_pulls_translates_and_presents_in_one_tick(tmp_path):
    event = canonical_message()
    raw_event = raw_observation("synthetic-raw")
    store, _recorder, sessions, _interpreter = registered_runtime(
        tmp_path, TranslationResult((event,), "translated")
    )
    harnesses = HarnessRegistry()
    harnesses.register(
        example_plugin(TranslationResult((event,), "translated"), (FixedReadSource((raw_event,)),))
    )
    interpreter = Interpreter(
        SessionRegistry(store.database_path, harnesses),
        harnesses,
        RawEventRecorder(store.database_path),
        WatchRegistry(store.database_path),
        store,
        NullControls(),
        NullTerminal(),
    )

    interpreter.tick()

    activity = SessionQueries(store, sessions).activity_after(
        SessionId("session-one"),
        0,
        ActivityScope(),
        10,
    ).activities[0]
    assert DashboardPresenter().present(activity).item_id == "message:actor-lead:message-one"
    terminal_update = TerminalPresenter().present(activity)
    assert terminal_update.updated_blocks[0].block_id == "message:actor-lead:message-one"


def test_one_failing_source_neither_stops_its_siblings_nor_the_interpreter(tmp_path, monkeypatch):
    """The interpreter drives every pulled source and nothing restarts it.

    An unguarded exception here once killed observation for EVERY session silently: the
    conversation stopped arriving while hooks (separate processes) kept flowing, so the
    session still looked alive.
    """
    audited = []
    monkeypatch.setattr(
        "app.interpreter._audit_failure",
        lambda where, context: audited.append((where, context)),
    )

    class BrokenSource:
        source_identity = "broken"

        def read(self, after_position):
            raise RuntimeError("this source is broken")

    event = canonical_message()
    raw_event = raw_observation("raw-one")
    store, _recorder, _sessions, _interpreter = registered_runtime(
        tmp_path, TranslationResult((event,), "translated")
    )
    harnesses = HarnessRegistry()
    harnesses.register(
        example_plugin(
            TranslationResult((event,), "translated"),
            (BrokenSource(), FixedReadSource((raw_event,))),
        )
    )
    interpreter = Interpreter(
        SessionRegistry(store.database_path, harnesses),
        harnesses,
        RawEventRecorder(store.database_path),
        WatchRegistry(store.database_path),
        store,
        NullControls(),
        NullTerminal(),
    )

    interpreter.tick()

    # The healthy sibling still drained, behind the broken one.
    assert len(store.after(SessionId("session-one"), 0, 10).events) == 1
    assert [where for where, _ in audited] == ["source read"]
    assert audited[0][1]["source_identity"] == "broken"


def test_watchable_is_every_unfinished_session_without_a_count_limit(tmp_path):
    database_path = str(tmp_path / "events.db")
    harnesses = HarnessRegistry()
    harnesses.register(example_plugin(TranslationResult((), "ignored_nonsemantic")))
    sessions = SessionRegistry(database_path, harnesses)
    store = CanonicalEventStore(database_path)
    for index in range(6):
        sessions.register("example", example_session(f"session-{index}"))

    assert len(sessions.watchable()) == 6
    assert all(session.plugin is not None for session in sessions.watchable())

    finish = CanonicalEvent(
        CanonicalEventId("finish-3"),
        SessionId("session-3"),
        ActorId("actor-lead"),
        None,
        None,
        "example",
        10.0,
        SessionFinished("succeeded", None),
    )
    RawEventRecorder(database_path).record((
        replace(raw_observation("raw-finish"), session_id=SessionId("session-3")),
    ))
    store.store_translation(
        replace(raw_observation("raw-finish"), session_id=SessionId("session-3")),
        "1.0",
        TranslationResult((finish,), "translated"),
    )

    watchable_ids = {str(session.session_id) for session in sessions.watchable()}
    assert "session-3" not in watchable_ids
    assert len(watchable_ids) == 5
    assert sessions.is_finished(SessionId("session-3"))


class RecordingTerminal:
    def __init__(self, session_window_id=None):
        self.session_window_id = session_window_id
        self.calls = []

    def close_session_panes(self, session_id):
        self.calls.append(("close", session_id))

    def session_panes_are_open(self, session_id):
        return any(call[0] == "open" for call in self.calls)

    def current_window(self):
        return "focused-window"

    def window_for_session(self, session_id):
        return self.session_window_id

    def open_session_panes(self, request):
        self.calls.append(("open", request.session_id, request.anchor_window_id))


def _pane_react_interpreter(tmp_path, observed_at, session_window_id=None):
    started = CanonicalEvent(
        CanonicalEventId("session-started"),
        SessionId("session-one"),
        ActorId("actor-lead"),
        None,
        None,
        "example",
        10.0,
        SessionStarted("/work", None, None, None, None, None),
    )
    database_path = str(tmp_path / "events.db")
    harnesses = HarnessRegistry()
    harnesses.register(example_plugin(TranslationResult((started,), "translated")))
    sessions = SessionRegistry(database_path, harnesses)
    sessions.register("example", example_session())
    recorder = RawEventRecorder(database_path)
    terminal = RecordingTerminal(session_window_id)
    interpreter = Interpreter(
        sessions,
        harnesses,
        recorder,
        WatchRegistry(database_path),
        CanonicalEventStore(database_path),
        NullControls(),
        terminal,
    )
    recorder.record((replace(raw_observation("raw-start"), observed_at=observed_at),))
    return interpreter, recorder, terminal


def test_the_interpreter_opens_panes_by_focus_only_for_a_fresh_session_start(tmp_path):
    import time as time_module

    interpreter, recorder, terminal = _pane_react_interpreter(
        tmp_path, observed_at=time_module.time()
    )

    interpreter.tick()
    # A replayed observation deduplicates and must NOT reopen panes.
    recorder.record((replace(raw_observation("raw-start-again"), source_position="1"),))
    interpreter.tick()

    assert terminal.calls == [("open", SessionId("session-one"), "focused-window")]


def test_the_interpreter_never_anchors_a_stale_session_start_by_focus(tmp_path):
    """The focus guess runs in the server, where the current window is wherever
    the user happens to be — a backlog replay must not spawn panes there."""
    interpreter, _recorder, terminal = _pane_react_interpreter(tmp_path, observed_at=11.0)

    interpreter.tick()

    assert terminal.calls == []


def test_the_interpreter_prefers_the_session_own_window_over_focus(tmp_path):
    interpreter, _recorder, terminal = _pane_react_interpreter(
        tmp_path, observed_at=11.0, session_window_id="session-tab-window"
    )

    interpreter.tick()

    assert terminal.calls == [("open", SessionId("session-one"), "session-tab-window")]


def test_watch_directives_run_the_whole_foreground_lifecycle(tmp_path):
    """start directive → active row → chunks pulled → finish directive → drained away."""
    database_path = str(tmp_path / "events.db")
    harnesses = HarnessRegistry()
    harnesses.register(example_plugin(TranslationResult((), "ignored_nonsemantic")))
    sessions = SessionRegistry(database_path, harnesses)
    sessions.register("example", example_session())
    recorder = RawEventRecorder(database_path)
    store = CanonicalEventStore(database_path)
    watches = WatchRegistry(database_path)
    interpreter = Interpreter(
        sessions, harnesses, recorder, watches, store, NullControls(), NullTerminal()
    )

    output_path = tmp_path / "operation.out"
    output_path.write_bytes(b"hello")
    context = RawEventSourceContext(
        session_id=SessionId("session-one"),
        lead_actor_id=ActorId("actor-lead"),
        actor_id=ActorId("actor-lead"),
        parent_actor_id=None,
        source_reference="fixture.jsonl",
    )
    watch = FileWatch(
        operation_id="operation-1",
        source_path=str(output_path),
        chunk_source_type="foreground_output",
        delete_source=True,
        initial_size=0,
        initial_modified_at=0,
        wait_for_source_change=False,
    )
    recorder.record((watch_start_raw_event(context, "example", watch),))
    interpreter.tick()  # applies the directive
    interpreter.tick()  # pulls the first chunks

    chunk_types = {
        raw.source_type
        for raw in EvidenceQueries(store).session(SessionId("session-one"))
    }
    assert "foreground_output" in chunk_types
    assert len(watches.for_session(SessionId("session-one"))) == 1

    recorder.record((watch_finish_raw_event(context, "example", "operation-1"),))
    interpreter.tick()
    interpreter.tick()

    assert watches.for_session(SessionId("session-one")) == ()
    assert not output_path.exists()
    assert store.untranslated_raw_events(10) == ()
