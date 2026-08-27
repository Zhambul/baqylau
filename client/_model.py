# client/_model.py — the pane's read model, folded from the daemon's stream.
#
# The daemon no longer renders anything for a pane; it serves SessionData and an
# append-only feed of entries, and this is the pane's copy of both. The browser
# holds the same two things in `sessions/shell-fold.ts` and folds them the same way,
# because the fold is a property of the FEED, not of a frontend: a command
# arrives as a start, some output chunks and a finish, and it is one block on
# any screen.
#
# The pane is a separate program and cannot import the API package. Its response
# models are declared here so malformed or changed frames stop at this boundary.
from __future__ import annotations

import time
from collections.abc import Iterator

from pydantic import BaseModel, ConfigDict


class WireModel(BaseModel):
    # A pane can outlive a daemon upgrade. It validates every field it reads and
    # ignores new response fields that this client version does not use.
    model_config = ConfigDict(extra="ignore")


class ContentRecord(WireModel):
    text: str = ""
    media_type: str = "text/plain"


class AccountRecord(WireModel):
    account_id: str = ""
    display_name: str = ""


class TaskRecord(WireModel):
    task_id: str = ""
    subject: str = ""
    description: str | None = None
    state: str = ""
    owner_actor_id: str | None = None


class GoalRecord(WireModel):
    objective: str | None = None
    state: str = ""
    reason: str | None = None
    completed: bool = False


class SessionRecord(WireModel):
    session_id: str = ""
    harness: str = ""
    title: str | None = None
    state: str = ""
    working_directory: str = ""
    started_at: float | None = None
    finished_at: float | None = None
    account: AccountRecord | None = None
    lead_actor_id: str = ""
    goal: GoalRecord | None = None
    tasks: tuple[TaskRecord, ...] = ()
    continued_from: str | None = None


class TokenRecord(WireModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    one_hour_cache_write_tokens: int = 0


class UsageRecord(WireModel):
    tokens: TokenRecord = TokenRecord()
    cost_in_usd: str | None = None


class ToolCountRecord(WireModel):
    tool: str
    count: int


class StatisticsRecord(WireModel):
    prompt_count: int = 0
    shell_command_count: int = 0
    failed_shell_command_count: int = 0
    file_count: int = 0
    lines_added: int = 0
    lines_removed: int = 0
    actor_message_count: int = 0
    tool_counts: tuple[ToolCountRecord, ...] = ()
    active_seconds: float = 0.0
    active: bool = False


class BackgroundRecord(WireModel):
    running_shell_ids: tuple[str, ...] = ()
    monitor_count: int = 0
    background_job_count: int = 0


class ContextRecord(WireModel):
    used_tokens: int = 0
    window_tokens: int = 0
    compacting: bool = False


class ActorRecord(WireModel):
    session_id: str = ""
    actor_id: str
    parent_actor_id: str | None = None
    role: str = ""
    name: str = ""
    description: str | None = None
    state: str = ""
    started_at: float | None = None
    finished_at: float | None = None
    model: str | None = None
    effort: str | None = None
    status: str | None = None
    background: BackgroundRecord = BackgroundRecord()
    statistics: StatisticsRecord = StatisticsRecord()
    usage: UsageRecord = UsageRecord()
    context: ContextRecord = ContextRecord()


class QuestionChoiceRecord(WireModel):
    label: str
    description: str | None = None


class QuestionRecord(WireModel):
    question_id: str = ""
    title: str | None = None
    question: str = ""
    multiple: bool = False
    choices: tuple[QuestionChoiceRecord, ...] = ()


class AnswerRecord(WireModel):
    question_id: str = ""
    labels: tuple[str, ...] = ()


class EntryBodyRecord(WireModel):
    message_id: str = ""
    reasoning_id: str = ""
    shell_id: str = ""
    command: ContentRecord | None = None
    execution: str | None = None
    content: ContentRecord | None = None
    stream: str | None = None
    mode: str | None = None
    state: str | None = None
    exit_code: int | None = None
    result: ContentRecord | None = None
    path: str | None = None
    previous_path: str | None = None
    action: str | None = None
    lines_added: int | None = None
    lines_removed: int | None = None
    tool: str | None = None
    query: ContentRecord | None = None
    url: str | None = None
    arguments: ContentRecord | None = None
    name: str | None = None
    skill_id: str = ""
    phase: str | None = None
    role: str | None = None
    recipient_actor_id: str | None = None
    reply_to: str | None = None
    attention_id: str | None = None
    questions: tuple[QuestionRecord, ...] = ()
    plan: ContentRecord | None = None
    answers: tuple[AnswerRecord, ...] = ()
    feedback: str | None = None
    edited: bool = False
    before_tokens: int | None = None
    after_tokens: int | None = None
    automatic: bool = False
    previous: str | None = None
    current: str | None = None
    assigned_actor_name: str | None = None
    assignment_id: str = ""
    prompt: ContentRecord | None = None


class EntryRecord(WireModel):
    entry_id: str
    type: str
    cursor: int
    actor_id: str
    parent_actor_id: str | None = None
    turn_id: str | None = None
    occurred_at: float
    summary: str | None = None
    body: EntryBodyRecord


class SnapshotDocument(WireModel):
    cursor: int
    session: SessionRecord
    actors: tuple[ActorRecord, ...]
    live: bool = False


class EntryPageDocument(WireModel):
    items: tuple[EntryRecord, ...]
    oldest_cursor: int = 0
    has_more: bool = False


class StreamFrameDocument(WireModel):
    session: SessionRecord | None = None
    actors: tuple[ActorRecord, ...] = ()
    entries: tuple[EntryRecord, ...] = ()

# What a question or a plan is answered BY. A pending one is an asked entry
# whose twin has not arrived — the whole of "this session is waiting on you",
# with no stored flag to go stale.
ATTENTION_TWINS = {"question_asked": "question_answered", "plan_proposed": "plan_resolved"}
# The feed's order is ONE list of keys over two dictionaries, and a prefix is
# what tells them apart: an entry id and a shell id are both harness-supplied
# strings and could collide.
_ENTRY_PREFIX = "entry:"
_SHELL_PREFIX = "shell:"
SHELL_ENTRIES = frozenset({
    "shell_started",
    "shell_output",
    "shell_backgrounded",
    "shell_finished",
})


class ShellFold:
    """One command: its start, every chunk it wrote, and how it ended.

    `mode == "replace"` is why the chunks cannot simply be concatenated — a
    harness that reports its whole output at once sends one replacing chunk, and
    appending it to what the file watch already streamed would double it.
    """

    def __init__(self, entry: EntryRecord) -> None:
        body = entry.body
        self.shell_id = body.shell_id
        self.command = _text(body.command)
        self.execution = body.execution or "foreground"
        self.output: str = ""
        self.status: str = ""
        self.state: str | None = None
        self.exit_code: int | None = None
        self.backgrounded = False
        self.started_at = entry.occurred_at
        self.finished_at: float | None = None

    def fold(self, entry: EntryRecord) -> None:
        body = entry.body
        if entry.type == "shell_output":
            text = _text(body.content)
            stream = "status" if body.stream == "status" else "output"
            current = self.status if stream == "status" else self.output
            value = text if body.mode == "replace" else current + text
            if stream == "status":
                self.status = value
            else:
                self.output = value
        elif entry.type == "shell_backgrounded":
            self.backgrounded = True
        elif entry.type == "shell_finished":
            self.state = body.state
            self.exit_code = body.exit_code
            self.finished_at = entry.occurred_at
            # A harness that streamed nothing reports the whole output here, and
            # it is folded exactly as a replacing chunk would be — because that
            # is what it is. Claude Code streams and leaves this empty; Codex
            # reports it once and streams nothing.
            result = _text(body.result)
            if result:
                self.output = result


def _text(content: ContentRecord | None) -> str:
    return content.text if content is not None else ""


class SessionModel:
    """SessionData plus the feed, at one cursor.

    The three inputs are the three shapes the daemon serves — the snapshot, a
    page of entries, and a stream frame — and each one lands through its own
    method so that nothing has to guess which it was given. Entry application is
    idempotent by `entry_id`: an overlapping frame after a reconnect is applied
    twice and shows once.
    """

    def __init__(self) -> None:
        self.cursor = 0
        self.session = SessionRecord()
        self.actors: dict[str, ActorRecord] = {}
        self.live = False
        # When this model last took a frame, on a monotonic clock. The only thing
        # it is for is carrying a running clock forward between frames: the
        # daemon measures elapsed time when it BUILDS a frame, and frames arrive
        # on change, not on a tick.
        self._framed_at = time.monotonic()
        # First-appearance order, and the two things that order can hold: an
        # entry as it arrived, or a command being folded. A shell takes the
        # position of its START and grows in place, which is what makes its
        # output land under its own command instead of at the end of the feed.
        self._order: list[str] = []
        self._entries: dict[str, EntryRecord] = {}
        self._shells: dict[str, ShellFold] = {}
        # Entries this model has already decided are dead. Remembered, not just
        # removed: a reconnect re-sends an overlapping page, and a discarded
        # prompt that was merely deleted would be re-admitted as news — and stay,
        # because the survivor that condemned it is applied only once.
        self._dropped: set[str] = set()

    # -- what arrives -----------------------------------------------------------

    def apply_snapshot(self, document: SnapshotDocument) -> None:
        document = SnapshotDocument.model_validate(document)
        self._framed_at = time.monotonic()
        self.cursor = document.cursor
        self.session = document.session
        self.live = document.live
        self.actors = {actor.actor_id: actor for actor in document.actors}

    def apply_page(self, document: EntryPageDocument) -> None:
        document = EntryPageDocument.model_validate(document)
        for entry in document.items:
            self._apply_entry(entry)

    def apply_frame(self, document: StreamFrameDocument) -> None:
        """One stream frame. Every part is absent when it did not change, so an
        actor that reported nothing keeps the row it already had — a frame is
        news, not a replacement world."""
        document = StreamFrameDocument.model_validate(document)
        self._framed_at = time.monotonic()
        if document.session is not None:
            self.session = document.session
        for actor in document.actors:
            self.actors[actor.actor_id] = actor
        for entry in document.entries:
            self._apply_entry(entry)

    def _apply_entry(self, entry: EntryRecord) -> None:
        entry_id = entry.entry_id
        if entry_id in self._entries or entry_id in self._dropped:
            return
        self.cursor = max(self.cursor, entry.cursor)
        self._entries[entry_id] = entry
        if entry.type in SHELL_ENTRIES:
            self._fold_shell(entry)
            return
        self._order.append(_ENTRY_PREFIX + entry_id)
        if entry.type == "message":
            self._drop_superseded(entry)

    def _fold_shell(self, entry: EntryRecord) -> None:
        shell_id = entry.body.shell_id
        fold = self._shells.get(shell_id)
        if fold is None:
            if entry.type != "shell_started":
                # Output for a command whose start is older than the page we
                # hold. It belongs to a block that is not on screen, so there is
                # nothing to grow and nothing to draw.
                return
            self._shells[shell_id] = ShellFold(entry)
            self._order.append(_SHELL_PREFIX + shell_id)
            return
        fold.fold(entry)

    def _drop_superseded(self, entry: EntryRecord) -> None:
        """A prompt that replaced another one takes its place.

        A harness that re-parents around a DISCARDED prompt reports both, and the
        only thing that tells them apart is that they name the same parent. The
        newest wins; the rest leave the feed, or the pane shows a prompt nobody
        sent for the rest of the session.
        """
        replaced = entry.body.reply_to
        if not replaced or not _is_prompt(entry):
            return
        surviving = entry.entry_id
        for key in list(self._order):
            if not key.startswith(_ENTRY_PREFIX):
                continue
            other = self._entries[key[len(_ENTRY_PREFIX):]]
            if other.entry_id == surviving or not _is_prompt(other):
                continue
            if other.body.reply_to == replaced:
                self._order.remove(key)
                del self._entries[other.entry_id]
                self._dropped.add(other.entry_id)

    # -- what a renderer asks ---------------------------------------------------

    def feed(self) -> Iterator[EntryRecord | ShellFold]:
        """The whole feed in arrival order: entries as they came, commands as
        folds. A renderer walks this and draws one row group per item."""
        for key in self._order:
            if key.startswith(_ENTRY_PREFIX):
                yield self._entries[key[len(_ENTRY_PREFIX):]]
            else:
                yield self._shells[key[len(_SHELL_PREFIX):]]

    def elapsed_since_frame(self) -> float:
        """Seconds since the last frame, on a monotonic clock.

        Monotonic and not wall time: a laptop that slept, or a clock the system
        stepped, would otherwise add hours to a running session's timer.
        """
        return max(0.0, time.monotonic() - self._framed_at)

    def actor(self, actor_id: str) -> ActorRecord | None:
        return self.actors.get(actor_id)

    def actor_name(self, actor_id: str) -> str:
        """The actor's own name, falling back to the id: an actor whose row has
        not arrived yet is still somebody who spoke."""
        actor = self.actor(actor_id)
        return actor.name if actor is not None and actor.name else actor_id

    def lead_actor_id(self) -> str:
        return self.session.lead_actor_id

    def lead(self) -> ActorRecord | None:
        return self.actor(self.lead_actor_id())

    def running_shell(self, shell_id: str) -> bool:
        """Whether the aggregate still counts this command as running.

        Asked of the ACTOR rather than of the fold: a background job whose launch
        already "finished" is still running, and only the aggregate knows.
        """
        for actor in self.actors.values():
            if shell_id in actor.background.running_shell_ids:
                return True
        return False

    def pending_attention(self) -> EntryRecord | None:
        """The question or plan still waiting on a person, if any."""
        answered = {
            entry.body.attention_id
            for entry in self._entries.values()
            if entry.type in ATTENTION_TWINS.values()
        }
        for entry in reversed(list(self.feed())):
            if isinstance(entry, ShellFold) or entry.type not in ATTENTION_TWINS:
                continue
            if entry.body.attention_id not in answered:
                return entry
        return None


def _is_prompt(entry: EntryRecord) -> bool:
    return entry.type == "message" and entry.body.role == "user"
