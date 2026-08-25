"""Contract tests for the canonical spine: record, interpret, react."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from dataclasses import replace

import pytest

from engine.interpret.translators import (
    InterruptTranslator,
    LivenessTranslator,
    ShellOutputTranslator,
)
from engine.interpret.interrupts import GRACE_SECONDS, PendingInterruptSource
from engine.interpret.liveness import (
    ProcessProbe,
    SessionLivenessSource,
    SessionWindowLivenessSource,
)
from engine.interpret.output_source import ShellOutputRawEventSource
from engine.interpret.loop import Interpreter, TerminalSnapshotSampler
from engine.react.loop import ReactionLoop
from engine.sessiondata.actors import (
    ActorWriter,
    ContextWriter,
    StatisticsWriter,
    StatusWriter,
    UsageWriter,
)
from engine.sessiondata.entries import EntryWriter
from engine.sessiondata.session import GoalWriter, SessionWriter, TaskWriter
from repository.impl.sqlite.session_data import SqliteSessionDataRepository
from app.raw_events_audit_cli import main as raw_event_audit_main
from engine.interpret.reactions import (
    InterruptCanonicalEventReaction,
    ShellOutputCanonicalEventReaction,
    SessionUpsertCanonicalEventReaction,
)
from terminal.panes.reaction import PaneCanonicalEventReaction
from terminal.models import SESSION_WINDOW_TAG, WindowInfo, WindowProcess
from harness.contract import HarnessPlugin
from harness.models import (
    HarnessInfo,
    INTERRUPT_SOURCE_TYPE,
    InterruptRegistry,
    LIVENESS_SOURCE_TYPE,
    OUTPUT_LOCATION_SOURCE_TYPE,
    RawEvent,
    RawEventSourceContext,
    Session,
    TranslationError,
    TranslationResult,
    output_location_raw_event,
)
from repository.mapper import facts as mapper
from repository.mapper.documents import StoredDocumentError, encode_document
from domain.events import (
    SCHEMA_VERSION,
    EVENT_TYPES,
    ActorAssignmentFinished,
    ActorAssignmentStarted,
    ActorFinished,
    ActorStarted,
    CanonicalEvent,
    MessageCreated,
    ShellFinished,
    ShellInputProvided,
    ShellOutputFinished,
    ShellOutputLocated,
    SessionFinished,
    SessionStarted,
    TurnAborted,
)
from domain.ids import (
    ActorId,
    AssignmentId,
    CanonicalEventId,
    HarnessName,
    MessageId,
    ShellId,
    RawEventId,
    SessionId,
    TabId,
    TurnId,
    WindowId,
    stable_event_id,
)
from domain.values import (
    ActorRole,
    MediaType,
    MessagePhase,
    MessageRole,
    Outcome,
    ShellFollowUntil,
    StructuredContent,
    TextContent,
)
from repository.model.facts import CanonicalEventRow
from audit.recorder import AuditRecorder
from harness.registry import HarnessRegistry, HarnessRegistryError
from repository.errors import EventIdentityConflict
from repository.impl.sqlite.canonical_events import SqliteCanonicalEventRepository
from repository.impl.sqlite.databases import main_database
from repository.impl.sqlite.raw_event_audits import SqliteRawEventAuditRepository
from repository.impl.sqlite.shell_output import SqliteShellOutputRepository
from repository.impl.sqlite.raw_events import SqliteRawEventRepository
from repository.impl.sqlite.sessions import SqliteSessionRepository

EXAMPLE_HARNESS = HarnessName.CODEX

# The liveness source checks the CLI pid against the CLI's process name; the
# suite's sessions carry the test process itself, so they read as alive.
OWN_PROCESS_NAME = os.path.basename(
    subprocess.run(
        ["ps", "-o", "comm=", "-p", str(os.getpid())],
        capture_output=True,
        text=True,
    ).stdout.strip()
)


class FixedTranslator:
    def __init__(self, translation: TranslationResult | TranslationError) -> None:
        self.translation = translation
        self.released: list[SessionId] = []

    def translate(self, raw_event):
        if isinstance(self.translation, TranslationError):
            raise self.translation
        return self.translation

    def release_session(self, session_id):
        self.released.append(session_id)


class FixedSources:
    def __init__(self, sources=()) -> None:
        self.fixed = sources
        self.released: list[SessionId] = []

    def for_session(self, session):
        return self.fixed

    def release_session(self, session_id):
        self.released.append(session_id)


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

    def open_session_panes(self, request):
        raise AssertionError("panes must not open when they are already open")


class NullControls:
    def execute(self, request):
        raise AssertionError(f"unexpected control: {request}")


def example_session(session_id: str = "session-one") -> Session:
    return Session(
        session_id=SessionId(session_id),
        lead_actor_id=ActorId("actor-lead"),
        source_reference="fixture.jsonl",
        working_directory="/work",
        harness_process_id=os.getpid(),
    )


def example_plugin(
    translation: TranslationResult | TranslationError,
    sources=(),
    name: HarnessName = HarnessName.CODEX,
) -> HarnessPlugin:
    return HarnessPlugin(
        info=HarnessInfo(name, name.value.title(), "1.0", SCHEMA_VERSION, OWN_PROCESS_NAME),
        sources=FixedSources(sources),
        translator=FixedTranslator(translation),
    )


def canonical_message(
    *,
    event_id: str = "event-message",
    session_id: str = "session-one",
    actor_id: str = "actor-lead",
    harness: HarnessName = EXAMPLE_HARNESS,
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
        terminal_window_id=None,
        harness_process_id=None,
        payload=MessageCreated(
            message_id=MessageId("message-one"),
            role=MessageRole.USER,
            content=TextContent(text),
            phase=MessagePhase.PROMPT,
            reply_to=None,
        ),
    )


def session_started_event(
    *,
    session_id: str = "session-one",
    terminal_window_id: WindowId | None = None,
    harness_process_id: int | None = None,
) -> CanonicalEvent:
    return CanonicalEvent(
        CanonicalEventId(f"session-started:{session_id}"),
        SessionId(session_id),
        ActorId("actor-lead"),
        None,
        None,
        HarnessName.CODEX,
        10.0,
        terminal_window_id,
        harness_process_id,
        SessionStarted("/work", "fixture.jsonl", None, None, None, None, None),
    )


def raw_observation(
    raw_event_id: str, *, harness: HarnessName = EXAMPLE_HARNESS, payload: bytes = b'{"kind":"message"}'
):
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


class _PaneWidths:
    """The pane reaction asks one question; a fixture need not store an answer."""

    @staticmethod
    def width_percent(working_directory) -> int:
        del working_directory
        return 25


class RecordingAudit(AuditRecorder):
    """The audit recorder, capturing instead of writing. The interpreter takes
    one by constructor, so a test that asserts on a swallowed failure holds the
    object it was handed rather than patching a module function."""

    def __init__(self):
        self.errors = []

    def error(self, session_or_log="", func="", context=None):
        self.errors.append((func, context))

    def failures(self):
        """The `where` of each swallowed interpreter failure."""
        return [func.removeprefix("interpreter (").removesuffix(")")
                for func, _context in self.errors]


SESSION_DATA_WRITERS = (
    SessionWriter(),
    GoalWriter(),
    TaskWriter(),
    ActorWriter(),
    StatusWriter(),
    UsageWriter(),
    ContextWriter(),
    StatisticsWriter(),
)


def _provenance(store, canonical_event):
    """Which raw observations a committed fact was built from.

    Two reads because they answer two questions: the reaction loop's page says
    WHAT was committed and in what order, and the store says which raw events
    converged on any one of those facts.
    """
    stored = store.find(canonical_event.event_id)
    assert stored is not None
    return stored.raw_event_ids


def build_interpreter(
    database_path, harnesses, *, terminal=None, controls=None, audit=None, interrupts=None
):
    """The bootstrap wiring, with an injectable terminal for the pane reaction."""
    database = main_database(str(database_path))
    sessions = SqliteSessionRepository(database, harnesses)
    recorder = SqliteRawEventRepository(database)
    store = SqliteCanonicalEventRepository(database)
    shell_output = SqliteShellOutputRepository(database)
    terminal = terminal if terminal is not None else NullTerminal()
    interrupts = interrupts if interrupts is not None else InterruptRegistry()
    # The interpreter's own two: the pull phase reads the rows they write.
    inputs = (
        SessionUpsertCanonicalEventReaction(sessions),
        ShellOutputCanonicalEventReaction(shell_output, recorder),
    )
    core_translators = {
        OUTPUT_LOCATION_SOURCE_TYPE: ShellOutputTranslator(),
        LIVENESS_SOURCE_TYPE: LivenessTranslator(),
        INTERRUPT_SOURCE_TYPE: InterruptTranslator(),
    }
    interpreter = Interpreter(
        sessions,
        harnesses,
        recorder,
        shell_output,
        store,
        core_translators,
        inputs,
        audit if audit is not None else RecordingAudit(),
        interrupts,
    )
    return interpreter, sessions, recorder, store, shell_output


def test_terminal_snapshot_sampler_uses_one_read_per_slow_interval():
    now = [0.0]

    class CountingTerminal:
        def __init__(self):
            self.calls = 0

        def windows(self):
            self.calls += 1
            return ()

    terminal = CountingTerminal()
    sampler = TerminalSnapshotSampler(terminal, lambda: now[0])

    sampler.sample()
    now[0] = 0.9
    sampler.sample()
    assert terminal.calls == 1

    now[0] = 1.0
    sampler.sample()
    assert terminal.calls == 2


def test_terminal_snapshot_sampler_reads_again_after_a_session_starts():
    now = [0.0]

    class CountingTerminal:
        def __init__(self):
            self.calls = 0

        def windows(self):
            self.calls += 1
            return ()

    terminal = CountingTerminal()
    sampler = TerminalSnapshotSampler(terminal, lambda: now[0])

    sampler.sample()
    now[0] = 0.1
    sampler.invalidate()
    sampler.sample()

    assert terminal.calls == 2


def test_an_accepted_session_start_invalidates_the_terminal_snapshot(tmp_path):
    store, recorder, _sessions, interpreter = registered_runtime(
        tmp_path,
        TranslationResult((session_started_event(),), "translated"),
    )

    class RecordingSnapshots:
        def __init__(self):
            self.invalidations = 0

        def sample(self):
            return ()

        def invalidate(self):
            self.invalidations += 1

    snapshots = RecordingSnapshots()
    interpreter.terminal_snapshots = snapshots
    recorder.record((raw_observation("raw-start"),))

    interpreter.tick()

    assert len(store.page_from(0, 10)) == 1
    assert snapshots.invalidations == 1


def build_reaction_loop(
    database_path,
    harnesses,
    *,
    terminal=None,
    interrupts=None,
    controls=None,
    audit=None,
):
    """The other loop, over the same database: what a committed fact CAUSES.

    A separate builder because it is a separate thread in the daemon — the
    interpreter appends facts and this follows them, and a test that wants both
    ticks both, in that order.
    """
    database = main_database(str(database_path))
    sessions = SqliteSessionRepository(database, harnesses)
    return ReactionLoop(
        SqliteCanonicalEventRepository(database),
        SqliteSessionDataRepository(database),
        (
            PaneCanonicalEventReaction(
                terminal if terminal is not None else NullTerminal(),
                sessions,
                _PaneWidths(),
            ),
            InterruptCanonicalEventReaction(
                interrupts if interrupts is not None else InterruptRegistry()
            ),
        ),
        EntryWriter(),
        SESSION_DATA_WRITERS,
        (),
        harnesses,
        controls if controls is not None else NullControls(),
        audit if audit is not None else RecordingAudit(),
    )


def registered_runtime(tmp_path, translation: TranslationResult | TranslationError, sources=()):
    database_path = str(tmp_path / "main.db")
    harnesses = HarnessRegistry()
    harnesses.register(example_plugin(translation, sources))
    interpreter, sessions, recorder, store, _shell_output = build_interpreter(
        database_path, harnesses
    )
    sessions.save(HarnessName.CODEX, example_session())
    return store, recorder, sessions, interpreter


@pytest.mark.parametrize(
    ("payload", "event_type", "expected_payload"),
    (
        (
            ActorStarted("weather researcher", ActorRole.CHILD),
            "actor.started",
            {"name": "weather researcher", "role": "child"},
        ),
        (
            ActorAssignmentStarted(
                AssignmentId("assignment-one"),
                TextContent("Get Bali weather"),
                actor_name="researcher",
                prompt=TextContent("Look up the weather in Bali.", MediaType.TEXT_MARKDOWN),
            ),
            "actor.assignment_started",
            {
                "assignment_id": "assignment-one",
                "brief": {"media_type": "text/plain", "text": "Get Bali weather"},
                "actor_name": "researcher",
                "prompt": {
                    "media_type": "text/markdown",
                    "text": "Look up the weather in Bali.",
                },
            },
        ),
        (
            ActorAssignmentFinished(
                AssignmentId("assignment-one"),
                Outcome.SUCCEEDED,
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
            ShellInputProvided(
                ShellId("operation-one"),
                TextContent("yes\n"),
                False,
            ),
            "shell.input_provided",
            {
                "shell_id": "operation-one",
                "content": {"media_type": "text/plain", "text": "yes\n"},
                "closed": False,
            },
        ),
    ),
)
def test_actor_lifecycle_payload_contract(payload, event_type, expected_payload):
    event = CanonicalEvent(
        event_id=CanonicalEventId("event-one"),
        session_id=SessionId("session-one"),
        actor_id=ActorId("actor-one"),
        turn_id=None,
        parent_actor_id=None,
        harness=HarnessName.CODEX,
        occurred_at=1.0,
        terminal_window_id=None,
        harness_process_id=None,
        payload=payload,
    )

    assert EVENT_TYPES[type(payload)] == event_type
    assert json.loads(mapper.payload_json(event)) == expected_payload
    assert "child" not in event_type


def test_a_repository_never_leaves_its_transaction_open(tmp_path):
    database = main_database(str(tmp_path / "main.db"))
    with database.write() as connection:
        opened_connection = connection
        connection.execute("CREATE TABLE example(value TEXT)")

    assert not opened_connection.in_transaction
    with database.read() as reused_connection:
        assert reused_connection is opened_connection
        assert reused_connection.execute("SELECT * FROM example").fetchall() == []


def test_a_saved_session_row_is_not_yet_a_canonical_session(tmp_path):
    sessions = SqliteSessionRepository(main_database(str(tmp_path / "main.db")))
    sessions.save(HarnessName.CODEX, example_session("candidate"))

    assert SqliteCanonicalEventRepository(main_database(str(tmp_path / "main.db"))).session_ids() == ()


def test_session_save_writes_identity_once_and_live_columns_always(tmp_path):
    """Identity columns are the first observation; the two live columns follow
    the session around — a resume lands in a new window with a new process."""
    sessions = SqliteSessionRepository(main_database(str(tmp_path / "main.db")))
    sessions.save(HarnessName.CODEX, replace(example_session(), terminal_window_id="window-1"))

    sessions.save(HarnessName.CODEX, replace(
        example_session(),
        working_directory="/elsewhere",
        terminal_window_id="window-2",
        harness_process_id=4242,
    ))

    loaded = sessions.find(SessionId("session-one"))
    assert loaded is not None
    assert loaded.working_directory == "/work"  # identity: first writer wins
    assert loaded.terminal_window_id == "window-2"  # live: last writer wins
    assert loaded.harness_process_id == 4242


def test_sessions_carry_their_plugin_only_when_harnesses_are_attached(tmp_path):
    database_path = str(tmp_path / "main.db")
    harnesses = HarnessRegistry()
    plugin = example_plugin(TranslationResult((), "ignored_nonsemantic"))
    harnesses.register(plugin)
    SqliteSessionRepository(main_database(database_path)).save(HarnessName.CODEX, example_session())

    recorder_side = SqliteSessionRepository(main_database(database_path)).find(SessionId("session-one"))
    server_side = SqliteSessionRepository(main_database(database_path), harnesses).find(SessionId("session-one"))

    assert recorder_side is not None and recorder_side.plugin is None
    assert server_side is not None and server_side.plugin is plugin
    assert recorder_side == server_side  # attachment, not identity
    assert SqliteSessionRepository(main_database(database_path)).find(SessionId("missing")) is None


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
    for name in (HarnessName.CODEX, HarnessName.CLAUDE_CODE):
        plugin = replace(
            example_plugin(TranslationResult((), "ignored_nonsemantic"), name=name),
            info=HarnessInfo(
                name, name.value.title(), "1", SCHEMA_VERSION, OWN_PROCESS_NAME,
                default_for_launch=True,
            ),
            launcher=object(),
        )
        if name == HarnessName.CODEX:
            registry.register(plugin)
        else:
            with pytest.raises(HarnessRegistryError, match="multiple harnesses"):
                registry.register(plugin)


def _row_from_values(values: tuple, cursor: int = 1) -> CanonicalEventRow:
    """`canonical_event_values`' tuple, as the row the store would hand back —
    the one thing missing from it is `cursor`, which only the AUTOINCREMENT
    column assigns."""
    (
        event_id, schema_version, event_type, session_id, actor_id, turn_id,
        parent_actor_id, harness, occurred_at, terminal_window_id,
        harness_process_id, accepted_at, payload,
    ) = values
    return CanonicalEventRow(
        cursor=cursor,
        event_id=CanonicalEventId(event_id),
        schema_version=schema_version,
        event_type=event_type,
        session_id=SessionId(session_id),
        actor_id=ActorId(actor_id),
        turn_id=TurnId(turn_id) if turn_id is not None else None,
        parent_actor_id=ActorId(parent_actor_id) if parent_actor_id is not None else None,
        harness=harness,
        occurred_at=occurred_at,
        terminal_window_id=terminal_window_id,
        harness_process_id=harness_process_id,
        accepted_at=accepted_at,
        payload=payload,
    )


def test_codec_round_trip_is_deterministic_and_structured_content_is_canonical():
    event = canonical_message()
    values = mapper.canonical_event_values(event, accepted_at=100.0)
    decoded = mapper.row_canonical_event(_row_from_values(values))
    assert replace(decoded, cursor=None, accepted_at=None) == event
    assert mapper.canonical_event_values(decoded, accepted_at=100.0) == values
    assert StructuredContent('{ "z": 1, "a": [true] }').json_text == '{"a":[true],"z":1}'


def test_codec_round_trips_the_observation_location_on_the_envelope():
    event = replace(
        canonical_message(), terminal_window_id="window-9", harness_process_id=1234
    )
    values = mapper.canonical_event_values(event, accepted_at=100.0)
    decoded = mapper.row_canonical_event(_row_from_values(values))
    assert decoded.terminal_window_id == "window-9"
    assert decoded.harness_process_id == 1234


def test_codec_rejects_an_unsupported_schema_version():
    row = replace(
        _row_from_values(mapper.canonical_event_values(canonical_message(), accepted_at=100.0)),
        schema_version=999,
    )
    with pytest.raises(StoredDocumentError, match="schema version"):
        mapper.row_canonical_event(row)


def test_codec_decodes_rows_written_before_a_defaulted_field_existed():
    # Additive schema evolution: a payload field with a declared default is
    # optional on decode, so stored events survive the field's introduction
    # without a rewrite. Fields without a default stay required, and extra
    # payload fields stay rejected.
    payload = ActorAssignmentStarted(
        AssignmentId("assignment-one"), TextContent("Get Bali weather")
    )
    event = CanonicalEvent(
        event_id=CanonicalEventId("event-one"),
        session_id=SessionId("session-one"),
        actor_id=ActorId("actor-one"),
        turn_id=None,
        parent_actor_id=None,
        harness=HarnessName.CODEX,
        occurred_at=1.0,
        terminal_window_id=None,
        harness_process_id=None,
        payload=payload,
    )
    event_type = EVENT_TYPES[type(payload)]
    document = json.loads(mapper.payload_json(event))
    del document["actor_name"]
    del document["prompt"]

    decoded = mapper.payload(event_type, json.dumps(document))

    assert decoded.actor_name is None
    assert decoded.prompt is None
    document["glyph"] = "x"
    with pytest.raises(StoredDocumentError, match="glyph"):
        mapper.payload(event_type, json.dumps(document))
    del document["glyph"]
    del document["brief"]
    with pytest.raises(StoredDocumentError, match="Field required"):
        mapper.payload(event_type, json.dumps(document))


def test_codec_rejects_an_invalid_payload_before_storage():
    event = canonical_message()
    invalid_payload = replace(event.payload, role="tool")

    with pytest.raises(StoredDocumentError, match="role"):
        mapper.encode_canonical_event(replace(event, payload=invalid_payload))


def test_stable_event_id_names_the_same_fact_and_distinguishes_its_phase():
    arguments = {
        "harness": "codex",
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


def test_evidence_translates_before_any_session_row_exists(tmp_path):
    """Facts may precede the session: there is no registration gate on the queue."""
    database_path = str(tmp_path / "main.db")
    recorder = SqliteRawEventRepository(main_database(database_path))
    recorder.record((raw_observation("raw-early"),))

    backlog = recorder.unverdicted(10)

    assert [raw.raw_event_id for raw in backlog] == [RawEventId("raw-early")]


def test_the_session_is_born_by_the_reaction_to_its_own_started_fact(tmp_path):
    """The whole point: nothing registers a session. Its first delivery
    translates into `session.started`, and the upsert reaction derives the row
    — identity from the payload, location from the envelope."""
    database_path = str(tmp_path / "main.db")
    harnesses = HarnessRegistry()
    started = session_started_event(
        terminal_window_id="the-session-tab", harness_process_id=4242
    )
    harnesses.register(example_plugin(TranslationResult((started,), "translated")))
    interpreter, sessions, recorder, store, _shell_output = build_interpreter(
        database_path, harnesses
    )
    recorder.record((raw_observation("raw-announcing"),))
    assert sessions.find(SessionId("session-one")) is None

    interpreter.tick()

    born = sessions.find(SessionId("session-one"))
    assert born is not None
    assert born.source_reference == "fixture.jsonl"
    assert born.working_directory == "/work"
    assert born.project_directory == "/work"
    assert born.terminal_window_id == "the-session-tab"
    assert born.harness_process_id == 4242
    assert born.plugin is harnesses.plugin(HarnessName.CODEX)
    assert recorder.unverdicted(10) == ()


def test_facts_before_the_started_fact_commit_but_birth_no_session(tmp_path):
    database_path = str(tmp_path / "main.db")
    harnesses = HarnessRegistry()
    harnesses.register(example_plugin(TranslationResult((canonical_message(),), "translated")))
    interpreter, sessions, recorder, store, _shell_output = build_interpreter(
        database_path, harnesses
    )
    recorder.record((raw_observation("raw-early"),))

    interpreter.tick()

    assert sessions.find(SessionId("session-one")) is None
    assert len(store.page_from(0, 10)) == 1


def test_a_later_delivery_updates_the_live_columns_of_the_row(tmp_path):
    """A resumed session shows up in a new window with a new process; the
    envelope of any later hook-borne fact refreshes the live columns."""
    database_path = str(tmp_path / "main.db")
    sessions = SqliteSessionRepository(main_database(database_path))
    sessions.save(HarnessName.CODEX, replace(
        example_session(), terminal_window_id="old-window", harness_process_id=1,
    ))
    reaction = SessionUpsertCanonicalEventReaction(sessions)

    reaction.react(replace(
        canonical_message(), terminal_window_id="new-window", harness_process_id=2,
    ))

    updated = sessions.find(SessionId("session-one"))
    assert updated is not None
    assert updated.terminal_window_id == "new-window"
    assert updated.harness_process_id == 2
    assert updated.project_directory == "/work"

    # A file-borne fact carries no location and touches nothing.
    reaction.react(canonical_message())
    untouched = sessions.find(SessionId("session-one"))
    assert untouched is not None and untouched.terminal_window_id == "new-window"


def test_a_file_start_does_not_erase_the_pid_from_the_hook_start(tmp_path):
    database_path = str(tmp_path / "main.db")
    sessions = SqliteSessionRepository(main_database(database_path))
    sessions.save(HarnessName.CODEX, replace(
        example_session(), terminal_window_id="session-window", harness_process_id=4242,
    ))
    reaction = SessionUpsertCanonicalEventReaction(sessions)

    reaction.react(session_started_event())

    updated = sessions.find(SessionId("session-one"))
    assert updated is not None
    assert updated.terminal_window_id == "session-window"
    assert updated.harness_process_id == 4242


def test_interpretation_commits_verdict_canonical_and_provenance_together(tmp_path):
    event = canonical_message()
    store, recorder, _sessions, interpreter = registered_runtime(
        tmp_path, TranslationResult((event,), "translated")
    )
    recorder.record((raw_observation("raw-one"),))

    interpreter.tick()

    assert recorder.unverdicted(10) == ()
    committed = store.page_from(0, 10)[0]
    assert replace(committed, cursor=None, accepted_at=None) == event
    connection = sqlite3.connect(store.sqlite_database.path)
    assert connection.execute("SELECT count(*) FROM raw_events").fetchone()[0] == 1
    assert connection.execute("SELECT decision FROM interpretations").fetchone()[0] == "translated"
    assert connection.execute(
        "SELECT event_order, storage_result FROM interpretation_events"
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

    stored = store.page_from(0, 10)
    assert len(stored) == 1
    # Provenance is asked of the fact by id: the reaction loop's page carries
    # what was committed, and which observations agreed on it is the store's own
    # record of that fact.
    assert _provenance(store, stored[0]) == (RawEventId("raw-one"), RawEventId("raw-two"))
    connection = sqlite3.connect(store.sqlite_database.path)
    assert connection.execute(
        "SELECT storage_result FROM interpretation_events WHERE raw_event_id='raw-two'"
    ).fetchone()[0] == "deduplicated"


def test_reused_raw_identity_is_corruption_not_convergence(tmp_path):
    recorder = SqliteRawEventRepository(main_database(str(tmp_path / "main.db")))
    recorder.record((raw_observation("raw-one"),))

    with pytest.raises(EventIdentityConflict, match="raw event identity reused"):
        recorder.record((raw_observation("raw-one", payload=b"different"),))


def test_re_observing_one_fact_is_idempotent_even_when_observers_disagree(tmp_path):
    """A canonical identity names a FACT, so re-observing it only audits the interpretation.

    Several sources legitimately converge on one event (a hook, the harness's
    own files, the foreground tee) and may render it differently. The first
    writer stays authoritative and the later rendering stays recoverable from
    its own raw evidence.
    """
    store, recorder, _sessions, _interpreter = registered_runtime(
        tmp_path, TranslationResult((), "ignored_nonsemantic")
    )
    recorder.record((raw_observation("raw-one"), raw_observation("raw-two")))
    store.record_translation(
        raw_observation("raw-one"),
        "1.0",
        TranslationResult((canonical_message(),), "translated"),
        1.0,
    )
    converged = store.record_translation(
        raw_observation("raw-two"),
        "1.0",
        TranslationResult((canonical_message(text="changed"),), "translated"),
        2.0,
    )

    # Converged re-observations are not returned as accepted, so a reaction
    # runs once per fact however many observers saw it.
    assert converged.accepted == ()
    assert [event.event_id for event in converged.deduplicated] == [
        CanonicalEventId("event-message")
    ]
    stored = store.page_from(0, 10)
    assert len(stored) == 1
    assert stored[0].payload.content.text == "hello"
    assert _provenance(store, stored[0]) == (RawEventId("raw-one"), RawEventId("raw-two"))
    # Nothing is lost: the disagreeing rendering survives as its own raw evidence.
    connection = sqlite3.connect(store.sqlite_database.path)
    assert connection.execute(
        "SELECT count(*) FROM raw_events WHERE raw_event_id='raw-two'"
    ).fetchone()[0] == 1


def test_translation_cannot_move_raw_evidence_to_another_actor(tmp_path):
    """A translator that rewrites raw-event envelope fields gets a failure verdict."""
    event = canonical_message(actor_id="actor-child")
    store, recorder, _sessions, interpreter = registered_runtime(
        tmp_path, TranslationResult((event,), "translated")
    )
    recorder.record((raw_observation("raw-child"),))

    interpreter.tick()

    connection = sqlite3.connect(store.sqlite_database.path)
    assert connection.execute("SELECT count(*) FROM canonical_events").fetchone()[0] == 0
    decision, reason = connection.execute(
        "SELECT decision, reason FROM interpretations WHERE raw_event_id='raw-child'"
    ).fetchone()
    assert decision == "translation_failed"
    assert "actor does not match" in reason


def test_translation_failure_is_a_complete_audited_decision(tmp_path):
    store, recorder, _sessions, interpreter = registered_runtime(
        tmp_path, TranslationError("malformed record", context="line 1")
    )
    recorder.record((raw_observation("raw-bad", payload=b"not json"),))

    interpreter.tick()

    connection = sqlite3.connect(store.sqlite_database.path)
    decision, reason = connection.execute(
        "SELECT decision, reason FROM interpretations WHERE raw_event_id='raw-bad'"
    ).fetchone()
    assert decision == "translation_failed"
    assert reason == "TranslationError: malformed record"
    assert connection.execute("SELECT count(*) FROM canonical_events").fetchone()[0] == 0


def test_a_translator_bug_becomes_a_verdict_and_never_wedges_the_backlog(tmp_path):
    """The backlog is ordered; an unverdicted row would block everything behind it."""

    class BuggyTranslator:
        def translate(self, raw_event):
            raise ZeroDivisionError("translator bug")

        def release_session(self, session_id):
            del session_id

    database_path = str(tmp_path / "main.db")
    harnesses = HarnessRegistry()
    harnesses.register(
        HarnessPlugin(
            info=HarnessInfo(HarnessName.CODEX, "Example", "1.0", SCHEMA_VERSION, OWN_PROCESS_NAME),
            sources=FixedSources(),
            translator=BuggyTranslator(),
        )
    )
    interpreter, sessions, recorder, store, _shell_output = build_interpreter(
        database_path, harnesses
    )
    sessions.save(HarnessName.CODEX, example_session())
    recorder.record((raw_observation("raw-bug"),))

    interpreter.tick()

    connection = sqlite3.connect(store.sqlite_database.path)
    decision, reason = connection.execute(
        "SELECT decision, reason FROM interpretations WHERE raw_event_id='raw-bug'"
    ).fetchone()
    assert decision == "translation_failed"
    assert "ZeroDivisionError" in reason
    assert recorder.unverdicted(10) == ()


def test_raw_event_audit_cli_prints_exact_raw_and_canonical_correlation(
    tmp_path, monkeypatch, capsys
):
    data_directory = tmp_path / "data"
    database_path = str(data_directory / "main.db")
    recorder = SqliteRawEventRepository(main_database(database_path))
    store = SqliteCanonicalEventRepository(main_database(database_path))
    raw_event = raw_observation("raw-one", payload=b"exact bytes\n")
    recorder.record((raw_event,))
    store.record_translation(raw_event, "1", TranslationResult((canonical_message(),), "translated"), 1.0)
    monkeypatch.setenv("BAQYLAU_DATA_DIR", str(data_directory))

    assert raw_event_audit_main(["raw", "raw-one"]) == 0

    document = json.loads(capsys.readouterr().out)
    assert document["payload_base64"] == "ZXhhY3QgYnl0ZXMK"
    assert document["canonical"][0]["event"]["event_id"] == "event-message"


def test_a_pulled_source_resumes_from_its_last_recorded_raw_event(tmp_path):
    """Progress is derived from the evidence itself, so it can never drift."""
    recorder = SqliteRawEventRepository(main_database(str(tmp_path / "main.db")))
    assert recorder.latest_positions(["fixture:source"]).get("fixture:source") is None

    recorder.record((
        raw_observation("raw-one"),
        replace(raw_observation("raw-two"), source_position="42"),
    ))

    assert recorder.latest_positions(["fixture:source"]).get("fixture:source") == "42"
    assert recorder.latest_positions(["someone:else"]).get("someone:else") is None


def test_raw_event_audit_shows_exact_raw_interpretation_and_canonical_chain(tmp_path):
    event = canonical_message()
    store, recorder, _sessions, interpreter = registered_runtime(
        tmp_path, TranslationResult((event,), "translated")
    )
    raw = raw_observation("raw-one")
    recorder.record((raw,))
    interpreter.tick()

    audit = SqliteRawEventAuditRepository(store.sqlite_database).audit(raw.raw_event_id)
    assert audit is not None
    assert audit.raw_event.payload == raw.payload
    assert audit.interpretation is not None
    assert audit.interpretation.decision == "translated"
    assert audit.interpretation.events[0].event.event_id == event.event_id
    assert audit.interpretation.events[0].event.actor_id == event.actor_id
    assert audit.interpretation.events[0].accepted_at > raw.observed_at
    assert audit.interpretation.completed_at == audit.interpretation.events[0].accepted_at
    assert audit.interpretation.events[0].storage_result == "accepted"
    assert SqliteRawEventAuditRepository(
        store.sqlite_database
    ).audits_for_session(SessionId("session-one")) == (audit,)


def test_raw_event_audit_shows_the_uninterpreted_backlog(tmp_path):
    recorder = SqliteRawEventRepository(main_database(str(tmp_path / "main.db")))
    store = SqliteCanonicalEventRepository(main_database(str(tmp_path / "main.db")))
    recorder.record((raw_observation("raw-waiting"),))

    audit = SqliteRawEventAuditRepository(store.sqlite_database).audit(RawEventId("raw-waiting"))

    assert audit is not None
    assert audit.interpretation is None


def test_the_interpreter_pulls_translates_and_commits_in_one_tick(tmp_path):
    event = canonical_message()
    raw_event = raw_observation("synthetic-raw")
    database_path = str(tmp_path / "main.db")
    harnesses = HarnessRegistry()
    harnesses.register(
        example_plugin(TranslationResult((event,), "translated"), (FixedReadSource((raw_event,)),))
    )
    interpreter, sessions, _recorder, store, _shell_output = build_interpreter(
        database_path, harnesses
    )
    sessions.save(HarnessName.CODEX, example_session())

    interpreter.tick()

    committed = store.page_from(0, 10)
    assert [item.event_id for item in committed] == [event.event_id]
    assert committed[0].payload == event.payload


def test_one_failing_source_neither_stops_its_siblings_nor_the_interpreter(tmp_path, monkeypatch):
    """The interpreter drives every pulled source and nothing restarts it.

    An unguarded exception here once killed observation for EVERY session silently: the
    conversation stopped arriving while hooks (separate processes) kept flowing, so the
    session still looked alive.
    """
    audited = RecordingAudit()

    class BrokenSource:
        source_identity = "broken"

        def read(self, after_position):
            raise RuntimeError("this source is broken")

    event = canonical_message()
    raw_event = raw_observation("raw-one")
    database_path = str(tmp_path / "main.db")
    harnesses = HarnessRegistry()
    harnesses.register(
        example_plugin(
            TranslationResult((event,), "translated"),
            (BrokenSource(), FixedReadSource((raw_event,))),
        )
    )
    interpreter, sessions, _recorder, store, _shell_output = build_interpreter(
        database_path, harnesses, audit=audited
    )
    sessions.save(HarnessName.CODEX, example_session())

    interpreter.tick()
    interpreter.tick()

    # The healthy sibling still drained, behind the broken one.
    assert len(store.page_from(0, 10)) == 1
    # A fast retry does not write the same full traceback again.
    assert audited.failures() == ["source read"]
    assert audited.errors[0][1]["source_identity"] == "broken"


def test_one_pull_cycle_reads_resume_positions_for_all_sessions_at_once(tmp_path, monkeypatch):
    database_path = str(tmp_path / "main.db")
    harnesses = HarnessRegistry()
    harnesses.register(example_plugin(TranslationResult((), "ignored_nonsemantic")))
    interpreter, sessions, recorder, _store, _shell_output = build_interpreter(
        database_path, harnesses
    )
    sessions.save(HarnessName.CODEX, example_session("session-one"))
    sessions.save(HarnessName.CODEX, example_session("session-two"))
    real_latest_positions = recorder.latest_positions
    calls: list[tuple[str, ...]] = []

    def recording_latest_positions(source_identities):
        calls.append(tuple(source_identities))
        return real_latest_positions(source_identities)

    monkeypatch.setattr(recorder, "latest_positions", recording_latest_positions)

    interpreter.tick()

    assert len(calls) == 1


def test_an_accepted_session_finish_releases_translator_memory(tmp_path):
    finish = CanonicalEvent(
        CanonicalEventId("finish-one"),
        SessionId("session-one"),
        ActorId("actor-lead"),
        None,
        None,
        HarnessName.CODEX,
        10.0,
        None,
        None,
        SessionFinished(Outcome.SUCCEEDED, None),
    )
    plugin = example_plugin(TranslationResult((finish,), "translated"))
    harnesses = HarnessRegistry()
    harnesses.register(plugin)
    interpreter, sessions, recorder, _store, _shell_output = build_interpreter(
        str(tmp_path / "main.db"), harnesses
    )
    sessions.save(HarnessName.CODEX, example_session())
    recorder.record((raw_observation("raw-finish"),))

    interpreter.tick()

    assert plugin.translator.released == [SessionId("session-one")]
    assert plugin.sources.released == [SessionId("session-one")]


def test_watchable_is_every_unfinished_session_without_a_count_limit(tmp_path):
    database_path = str(tmp_path / "main.db")
    harnesses = HarnessRegistry()
    harnesses.register(example_plugin(TranslationResult((), "ignored_nonsemantic")))
    sessions = SqliteSessionRepository(main_database(database_path), harnesses)
    store = SqliteCanonicalEventRepository(main_database(database_path))
    for index in range(6):
        sessions.save(HarnessName.CODEX, example_session(f"session-{index}"))

    assert len(sessions.watchable()) == 6
    assert all(session.plugin is not None for session in sessions.watchable())

    finish = CanonicalEvent(
        CanonicalEventId("finish-3"),
        SessionId("session-3"),
        ActorId("actor-lead"),
        None,
        None,
        HarnessName.CODEX,
        10.0,
        None,
        None,
        SessionFinished(Outcome.SUCCEEDED, None),
    )
    SqliteRawEventRepository(main_database(database_path)).record((
        replace(raw_observation("raw-finish"), session_id=SessionId("session-3")),
    ))
    store.record_translation(
        replace(raw_observation("raw-finish"), session_id=SessionId("session-3")),
        "1.0",
        TranslationResult((finish,), "translated"),
        1.0,
    )

    watchable_ids = {str(session.session_id) for session in sessions.watchable()}
    assert "session-3" not in watchable_ids
    assert len(watchable_ids) == 5


# --- liveness ------------------------------------------------------------------


def test_a_pid_less_session_is_a_loud_audited_error_every_tick(tmp_path, monkeypatch):
    """Never a silent skip: a session without a harness process id cannot be
    watched for liveness, and the failure lands in the audit until it can."""
    audited = RecordingAudit()
    database_path = str(tmp_path / "main.db")
    harnesses = HarnessRegistry()
    harnesses.register(example_plugin(TranslationResult((), "ignored_nonsemantic")))
    interpreter, sessions, _recorder, _store, _shell_output = build_interpreter(
        database_path, harnesses, audit=audited
    )
    sessions.save(HarnessName.CODEX, replace(example_session(), harness_process_id=None))

    interpreter.tick()

    assert audited.failures() == ["source construction"]


def test_a_dead_cli_process_becomes_one_session_finished_fact(tmp_path):
    """The liveness source is the one finish signal every session has."""
    database_path = str(tmp_path / "main.db")
    harnesses = HarnessRegistry()
    harnesses.register(example_plugin(TranslationResult((), "ignored_nonsemantic")))
    interpreter, sessions, _recorder, store, _shell_output = build_interpreter(
        database_path, harnesses
    )
    # A pid that is certainly not a live process with our name.
    sessions.save(HarnessName.CODEX, replace(example_session(), harness_process_id=2**22 + 1))

    interpreter.tick()

    events = store.page_from(0, 10)
    assert [type(stored.payload) for stored in events] == [SessionFinished]
    assert stored_reason(events[0]) == "process_exited"
    assert sessions.watchable() == ()

    # The latch: a later tick re-records nothing.
    interpreter.tick()
    connection = sqlite3.connect(store.sqlite_database.path)
    assert connection.execute(
        "SELECT count(*) FROM raw_events WHERE source_type='liveness'"
    ).fetchone()[0] == 1


def test_a_dead_run_finishes_before_its_rollout_batch_is_interpreted(tmp_path):
    """A stale large rollout cannot hold a newer session behind its exit."""
    rollout = raw_observation("stale-rollout")
    harnesses = HarnessRegistry()
    harnesses.register(
        example_plugin(
            TranslationResult((canonical_message(),), "translated"),
            sources=(FixedReadSource((rollout,)),),
        )
    )
    interpreter, sessions, _recorder, store, _shell_output = build_interpreter(
        str(tmp_path / "main.db"), harnesses
    )
    sessions.save(
        HarnessName.CODEX,
        replace(
            example_session(),
            terminal_window_id=WindowId("stale-window"),
            harness_process_id=2**22 + 1,
        ),
    )

    interpreter.tick()

    assert [type(event.payload) for event in store.page_from(0, 10)] == [
        SessionFinished,
        MessageCreated,
    ]


def test_each_native_run_gets_its_own_process_exit_fact():
    """A resumed session is one lineage but each terminal window is one run."""
    plugin = example_plugin(TranslationResult((), "ignored_nonsemantic"))

    def finished_event(window_id: str, process_id: int):
        session = replace(
            example_session(),
            plugin=plugin,
            terminal_window_id=WindowId(window_id),
            harness_process_id=process_id,
        )
        raw_event = SessionLivenessSource(session, ProcessProbe()).read(None)[0]
        return LivenessTranslator().translate(raw_event).canonical_events[0]

    first = finished_event("window-one", 2**22 + 1)
    same_run = finished_event("window-one", 2**22 + 2)
    resumed = finished_event("window-two", 2**22 + 3)

    assert first.event_id == same_run.event_id
    assert resumed.event_id != first.event_id
    assert first.terminal_window_id == WindowId("window-one")
    assert resumed.terminal_window_id == WindowId("window-two")


def stored_reason(stored) -> str | None:
    return stored.payload.reason


def test_the_liveness_source_verifies_the_process_is_still_the_cli():
    """Pids get reused by the OS; alive is not enough."""
    session = replace(example_session(), plugin=example_plugin(
        TranslationResult((), "ignored_nonsemantic")
    ))
    alive = SessionLivenessSource(session, ProcessProbe())
    assert alive.read(None) == ()  # our own pid, our own process name: alive

    imposter = replace(
        session,
        plugin=replace(
            session.plugin,
            info=HarnessInfo(HarnessName.CODEX, "Example", "1.0", SCHEMA_VERSION, "definitely-not-us"),
        ),
    )
    raw_events = SessionLivenessSource(imposter, ProcessProbe()).read(None)
    assert [raw.source_type for raw in raw_events] == [LIVENESS_SOURCE_TYPE]

    with pytest.raises(ValueError, match="no harness process id"):
        SessionLivenessSource(replace(session, harness_process_id=None), ProcessProbe())


def test_terminal_ownership_transfer_finishes_the_old_session():
    session = replace(
        example_session(),
        plugin=example_plugin(TranslationResult((), "ignored_nonsemantic")),
        terminal_window_id=WindowId("window-one"),
        harness_process_id=4242,
    )
    window = WindowInfo(
        WindowId("window-one"),
        TabId("tab-one"),
        {SESSION_WINDOW_TAG: "session-two"},
        120,
        40,
        True,
        True,
        True,
        processes=(WindowProcess(4242, ("/opt/codex",)),),
    )

    raw_event = SessionLivenessSource(session, ProcessProbe(), (window,)).read(None)[0]

    assert raw_event.source_position == "displaced"
    translation = LivenessTranslator().translate(raw_event)
    assert len(translation.canonical_events) == 1
    assert translation.canonical_events[0].payload.reason == "terminal_reassigned"


def test_a_copied_terminal_id_does_not_displace_its_session():
    session = replace(
        example_session(),
        plugin=example_plugin(TranslationResult((), "ignored_nonsemantic")),
        terminal_window_id=WindowId("window-one"),
        harness_process_id=os.getpid(),
    )
    window = WindowInfo(
        WindowId("window-one"),
        TabId("tab-one"),
        {SESSION_WINDOW_TAG: "session-two"},
        120,
        40,
        True,
        True,
        True,
        processes=(WindowProcess(4242, ("/opt/codex",)),),
    )

    assert SessionLivenessSource(session, ProcessProbe(), (window,)).read(None) == ()


def test_resume_liveness_waits_for_the_session_tag_while_the_cli_is_present():
    session = replace(
        example_session(),
        plugin=example_plugin(TranslationResult((), "ignored_nonsemantic")),
        terminal_window_id=WindowId("window-one"),
        harness_process_id=None,
    )
    window = WindowInfo(
        WindowId("window-one"),
        TabId("tab-one"),
        {},
        120,
        40,
        True,
        True,
        True,
        processes=(WindowProcess(None, (f"/opt/{OWN_PROCESS_NAME}", "resume")),),
    )

    assert SessionWindowLivenessSource(session, (window,)).read(None) == ()


def test_resume_liveness_finishes_a_missing_or_reassigned_window():
    session = replace(
        example_session(),
        plugin=example_plugin(TranslationResult((), "ignored_nonsemantic")),
        terminal_window_id=WindowId("window-one"),
        harness_process_id=None,
    )
    reassigned = WindowInfo(
        WindowId("window-one"),
        TabId("tab-one"),
        {SESSION_WINDOW_TAG: "session-two"},
        120,
        40,
        True,
        True,
        True,
        processes=(WindowProcess(None, (f"/opt/{OWN_PROCESS_NAME}", "resume")),),
    )

    missing = SessionWindowLivenessSource(session, ()).read(None)
    transferred = SessionWindowLivenessSource(session, (reassigned,)).read(None)

    assert [event.source_position for event in missing] == ["exited"]
    assert [event.source_position for event in transferred] == ["exited"]


def test_the_probe_pays_for_the_name_check_once(monkeypatch):
    """After one verified name check, a probe is a signal-0 syscall — the sources
    are rebuilt every tick, so the memory has to survive on the probe itself."""
    session = replace(example_session(), plugin=example_plugin(
        TranslationResult((), "ignored_nonsemantic")
    ))
    probe = ProcessProbe()
    name_checks = []
    monkeypatch.setattr(
        "engine.interpret.liveness.process_alive",
        lambda pid, name: name_checks.append(pid) or True,
    )
    assert SessionLivenessSource(session, probe).read(None) == ()
    assert SessionLivenessSource(session, probe).read(None) == ()
    assert len(name_checks) == 1


def test_pending_interrupt_source_waits_out_the_grace_period_then_latches():
    session = replace(example_session(), plugin=example_plugin(
        TranslationResult((), "ignored_nonsemantic")
    ))
    registry = InterruptRegistry()
    source = PendingInterruptSource(session, registry)

    # Nothing marked: no fallback fact is ever manufactured for a session
    # nobody interrupted.
    assert source.read(None) == ()

    registry.mark(session.session_id)
    # Still inside the grace period: genuine harness evidence gets first say.
    assert source.read(None) == ()

    # Fake the passage of the grace period by marking with a past timestamp.
    registry._marked_at[session.session_id] -= GRACE_SECONDS + 1
    raw_events = source.read(None)
    assert [raw.source_type for raw in raw_events] == [INTERRUPT_SOURCE_TYPE]

    # The latch: the position just emitted is not re-emitted.
    assert source.read(raw_events[0].source_position) == ()

    with pytest.raises(ValueError, match="no attached harness plugin"):
        PendingInterruptSource(replace(session, plugin=None), registry)


def test_an_uncorroborated_interrupt_eventually_clears_the_busy_state(tmp_path):
    """The bug this whole mechanism exists for: a harness whose Stop-equivalent
    signal never distinguishes an interrupted turn from a completed one leaves
    a session looking busy forever unless something else settles the turn."""
    database_path = tmp_path / "main.db"
    harnesses = HarnessRegistry()
    harnesses.register(example_plugin(TranslationResult((), "ignored_nonsemantic")))
    registry = InterruptRegistry()
    interpreter, sessions, _recorder, store, _shell_output = build_interpreter(
        database_path, harnesses, interrupts=registry
    )
    reactions = build_reaction_loop(database_path, harnesses, interrupts=registry)
    sessions.save(HarnessName.CODEX, example_session())

    interpreter.tick()  # sees no evidence yet: nothing to abort

    registry.mark(SessionId("session-one"))
    registry._marked_at[SessionId("session-one")] -= GRACE_SECONDS + 1

    interpreter.tick()

    events = store.page_from(0, 10)
    assert [type(stored.payload) for stored in events] == [TurnAborted]
    reactions.tick()
    # The reaction cleared the mark once the fact was committed AND followed.
    assert registry.pending(SessionId("session-one")) is None

    # The latch: a later tick manufactures nothing further.
    interpreter.tick()
    events_after = store.page_from(0, 10)
    assert len(events_after) == 1


# --- panes ----------------------------------------------------------------------


class RecordingTerminal:
    def __init__(self):
        self.calls = []

    def close_session_panes(self, session_id):
        self.calls.append(("close", session_id))

    def session_panes_are_open(self, session_id):
        return any(call[0] == "open" for call in self.calls)

    def open_session_panes(self, request):
        self.calls.append(("open", request.session_id, request.anchor_window_id))

    def window_hosts_process(self, window_id, process_id, process_name):
        self.calls.append(("ownership", window_id, process_id, process_name))
        return True


def _pane_react_interpreter(tmp_path, *, window_id=None):
    started = session_started_event(
        terminal_window_id=window_id, harness_process_id=os.getpid()
    )
    database_path = str(tmp_path / "main.db")
    harnesses = HarnessRegistry()
    harnesses.register(example_plugin(TranslationResult((started,), "translated")))
    terminal = RecordingTerminal()
    interpreter, _sessions, recorder, _store, _shell_output = build_interpreter(
        database_path, harnesses, terminal=terminal
    )
    reactions = build_reaction_loop(database_path, harnesses, terminal=terminal)
    recorder.record((raw_observation("raw-start"),))
    return interpreter, reactions, recorder, terminal


def test_panes_open_at_the_window_the_announcing_delivery_recorded(tmp_path):
    """The envelope of the session.started fact carries the window the hook ran
    in. Two loops, in order: the interpreter commits the fact and writes the
    sessions row, and the reaction loop — following the same fact through the
    canonical cursor — anchors the panes to it."""
    interpreter, reactions, recorder, terminal = _pane_react_interpreter(
        tmp_path, window_id="the-session-tab"
    )

    interpreter.tick()
    reactions.tick()
    # A replayed observation deduplicates, so the reaction loop never sees it a
    # second time and must NOT reopen panes.
    recorder.record((replace(raw_observation("raw-start-again"), source_position="1"),))
    interpreter.tick()
    reactions.tick()

    assert terminal.calls == [
        ("ownership", "the-session-tab", os.getpid(), OWN_PROCESS_NAME),
        ("open", SessionId("session-one"), "the-session-tab"),
    ]


def test_a_headless_session_gets_no_panes(tmp_path):
    interpreter, reactions, _recorder, terminal = _pane_react_interpreter(tmp_path)

    interpreter.tick()
    reactions.tick()

    assert terminal.calls == []


def test_a_continuation_moves_the_panes_from_the_prior_session():
    terminal = RecordingTerminal()

    class Sessions:
        def find(self, session_id):
            if session_id != SessionId("session-new"):
                return None
            return type("SessionRow", (), {
                "terminal_window_id": "the-session-tab",
                "harness_process_id": 4242,
                "working_directory": "/work",
                "plugin": type("Plugin", (), {
                    "info": type("Info", (), {"cli_process_name": "codex"})(),
                })(),
            })()

    reaction = PaneCanonicalEventReaction(terminal, Sessions(), _PaneWidths())
    started = replace(
        session_started_event(session_id="session-new"),
        payload=replace(
            session_started_event().payload,
            continued_from=SessionId("session-old"),
        ),
    )

    reaction.react(started)

    assert terminal.calls == [
        ("close", SessionId("session-old")),
        ("ownership", "the-session-tab", 4242, "codex"),
        ("open", SessionId("session-new"), "the-session-tab"),
    ]


# --- operation output -------------------------------------------------------------


def test_output_location_directives_run_the_whole_foreground_lifecycle(tmp_path):
    """directive → fact → active row → chunks pulled → operation.finished → drained away."""
    database_path = str(tmp_path / "main.db")
    harnesses = HarnessRegistry()
    finished = CanonicalEvent(
        CanonicalEventId("operation-finished"),
        SessionId("session-one"),
        ActorId("actor-lead"),
        None,
        None,
        HarnessName.CODEX,
        10.0,
        None,
        None,
        ShellFinished(ShellId("operation-1"), Outcome.SUCCEEDED, None, None),
    )
    harnesses.register(example_plugin(TranslationResult((finished,), "translated")))
    interpreter, sessions, recorder, store, shell_output = build_interpreter(
        database_path, harnesses
    )
    sessions.save(HarnessName.CODEX, example_session())

    output_path = tmp_path / "operation.out"
    output_path.write_bytes(b"hello")
    context = RawEventSourceContext(
        session_id=SessionId("session-one"),
        lead_actor_id=ActorId("actor-lead"),
        actor_id=ActorId("actor-lead"),
        parent_actor_id=None,
        source_reference="fixture.jsonl",
    )
    located = ShellOutputLocated(
        shell_id=ShellId("operation-1"),
        source_path=str(output_path),
        chunk_source_type="tool_output",
        delete_source=True,
        initial_size=0,
        initial_modified_at=0,
        wait_for_source_change=False,
        until=ShellFollowUntil.SHELL_FINISHED,
    )
    recorder.record((
        output_location_raw_event(context, HarnessName.CODEX, located, payload=encode_document(located)),
    ))
    interpreter.tick()  # translates the directive; the reaction starts the following
    interpreter.tick()  # pulls the first chunks

    chunk_types = {
        audit.raw_event.source_type
        for audit in SqliteRawEventAuditRepository(store.sqlite_database).audits_for_session(
            SessionId("session-one")
        )
    }
    assert "tool_output" in chunk_types
    assert len(shell_output.find_for_session(SessionId("session-one"))) == 1
    committed_types = {
        type(stored.payload)
        for stored in store.page_from(0, 100)
    }
    assert ShellOutputLocated in committed_types

    # The operation.finished fact (from the plugin translator) ends the following.
    recorder.record((raw_observation("raw-finish"),))
    interpreter.tick()
    interpreter.tick()

    assert shell_output.find_for_session(SessionId("session-one")) == ()
    assert not output_path.exists()
    assert recorder.unverdicted(10) == ()


def test_a_background_following_survives_operation_finished_until_the_session_ends(tmp_path):
    database_path = str(tmp_path / "main.db")
    sessions = SqliteSessionRepository(main_database(database_path))
    recorder = SqliteRawEventRepository(main_database(database_path))
    shell_output = SqliteShellOutputRepository(main_database(database_path))
    reaction = ShellOutputCanonicalEventReaction(shell_output, recorder)
    output_path = tmp_path / "task.output"
    output_path.write_bytes(b"background bytes")
    located = ShellOutputLocated(
        shell_id=ShellId("operation-bg"),
        source_path=str(output_path),
        chunk_source_type="tool_output",
        delete_source=False,
        initial_size=0,
        initial_modified_at=0,
        wait_for_source_change=False,
        until=ShellFollowUntil.SESSION_FINISHED,
    )
    reaction.react(replace(canonical_message(), payload=located))
    reaction.react(replace(
        canonical_message(),
        payload=ShellFinished(ShellId("operation-bg"), Outcome.SUCCEEDED, None, None),
    ))
    assert len(shell_output.find_for_session(SessionId("session-one"))) == 1

    sessions.save(HarnessName.CODEX, example_session())
    reaction.react(replace(canonical_message(), payload=SessionFinished(Outcome.SUCCEEDED, None)))

    assert shell_output.find_for_session(SessionId("session-one")) == ()
    assert output_path.exists()  # the harness's own file is never deleted by us
    connection = sqlite3.connect(database_path)
    assert connection.execute(
        "SELECT count(*) FROM raw_events WHERE source_type='tool_output'"
    ).fetchone()[0] == 1  # the tail was drained before the row was removed


def test_the_background_completion_fact_ends_the_following_early(tmp_path):
    """`operation.output_finished` is the background job's true end: the
    following stops there instead of stat-ing the file until the session dies."""
    database_path = str(tmp_path / "main.db")
    recorder = SqliteRawEventRepository(main_database(database_path))
    shell_output = SqliteShellOutputRepository(main_database(database_path))
    reaction = ShellOutputCanonicalEventReaction(shell_output, recorder)
    output_path = tmp_path / "task.output"
    output_path.write_bytes(b"background bytes")
    located = ShellOutputLocated(
        shell_id=ShellId("operation-bg"),
        source_path=str(output_path),
        chunk_source_type="tool_output",
        delete_source=False,
        initial_size=0,
        initial_modified_at=0,
        wait_for_source_change=False,
        until=ShellFollowUntil.SESSION_FINISHED,
    )
    reaction.react(replace(canonical_message(), payload=located))
    # the launch-time operation.finished must NOT end a background following…
    reaction.react(replace(
        canonical_message(),
        payload=ShellFinished(ShellId("operation-bg"), Outcome.SUCCEEDED, None, None),
    ))
    assert len(shell_output.find_for_session(SessionId("session-one"))) == 1

    # …but the completion notification's fact does
    reaction.react(replace(
        canonical_message(),
        payload=ShellOutputFinished(ShellId("operation-bg")),
    ))
    followings = shell_output.find_for_session(SessionId("session-one"))
    assert len(followings) == 1  # one final drain still owed
    raw_events = ShellOutputRawEventSource(followings[0], shell_output).read(None)
    recorder.record(raw_events)
    assert raw_events[-1].source_position == "finished"

    assert shell_output.find_for_session(SessionId("session-one")) == ()
    assert output_path.exists()  # the harness's own file is never deleted by us
