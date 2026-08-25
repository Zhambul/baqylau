"""Small immutable values used by canonical event payloads."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias

from domain.ids import AccountId, QuestionId
from domain.stored import STORED


class Outcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class ActorRole(StrEnum):
    LEAD = "lead"
    CHILD = "child"
    TEAMMATE = "teammate"
    SIDECAR = "sidecar"


class ExecutionMode(StrEnum):
    FOREGROUND = "foreground"
    BACKGROUND = "background"
    MONITOR = "monitor"


class MessageRole(StrEnum):
    """Who said it, and where the saying sits in a turn. Named because the
    harness translators BUILD both out of native JSON and are checked against
    these same lists; spelled inline on the payload they would only ever be
    checked at the constructor call."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    PEER = "peer"
    PARENT = "parent"


class MessagePhase(StrEnum):
    """`end_turn` names what the raw event says — the message a model STOPPED
    on, which every harness reports on the response itself — rather than what
    a reader might hope it means. It is deliberately NOT "the one answer of a
    turn": a turn that an injection resumes stops more than once, and each of
    those messages ended a response. A presenter that wants "the turn's final
    answer" derives it; the fact recorded here is the stop."""

    PROMPT = "prompt"
    INTERMEDIATE = "intermediate"
    END_TURN = "end_turn"
    SYNTHETIC = "synthetic"
    RECAP = "recap"


class FileAction(StrEnum):
    """What a file was done to. The translators map a native vocabulary onto
    this, and the mapping table is the thing that has to be checked."""

    READ = "read"
    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"
    RENAMED = "renamed"


# The rest of the closed sets a translator has to produce. Every one of these
# was an inline Literal on its payload, which meant the mapping that built it
# was typed `str` and checked nowhere.


class PlanState(StrEnum):
    """How a proposed plan ended. A question's end carries no such verdict:
    what a person answered is the answer itself, and the harnesses' own
    decision words (answered / rejected / discussed) collapsed to one line in
    every reader that ever had them."""

    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    REJECTED = "rejected"


class ModelChangeReason(StrEnum):
    """Why a model changed. Named because the translators keep the last-seen
    value and build the change event from it (harness/models/selections.py),
    so the reason travels as an argument and an argument that is typed `str`
    is checked nowhere."""

    SELECTED = "selected"
    AUTOMATIC_FALLBACK = "automatic_fallback"
    REPORTED_BY_HARNESS = "reported_by_harness"


class EffortChangeReason(StrEnum):
    SELECTED = "selected"
    REPORTED_BY_HARNESS = "reported_by_harness"


class WorktreeAction(StrEnum):
    ENTERED = "entered"
    EXITED = "exited"


class ProgressStream(StrEnum):
    OUTPUT = "output"
    ERROR = "error"
    STATUS = "status"


class OpenWorkKind(StrEnum):
    TURN = "turn"
    SHELL = "shell"
    ASSIGNMENT = "assignment"


class TitleOrigin(StrEnum):
    CUSTOM = "custom"
    AUTOMATIC = "automatic"
    SUMMARY = "summary"


class GoalState(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    BLOCKED = "blocked"
    USAGE_LIMITED = "usage_limited"
    BUDGET_LIMITED = "budget_limited"
    COMPLETED = "completed"
    CLEARED = "cleared"


class OutputMode(StrEnum):
    """How a chunk of streamed output joins what came before it."""

    APPEND = "append"
    REPLACE = "replace"


class ShellFollowUntil(StrEnum):
    """When a followed output file stops being followed."""

    SHELL_FINISHED = "shell_finished"
    SESSION_FINISHED = "session_finished"


class TaskState(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    DELETED = "deleted"


class UsageScope(StrEnum):
    """What one usage report's numbers are for."""

    SESSION = "session"
    ACTOR = "actor"
    TURN = "turn"
    OPERATION = "operation"


class MediaType(StrEnum):
    TEXT_PLAIN = "text/plain"
    TEXT_MARKDOWN = "text/markdown"


@dataclass(frozen=True)
class ModelReference:
    __pydantic_config__ = STORED

    name: str
    display_name: str | None


@dataclass(frozen=True)
class AccountReference:
    __pydantic_config__ = STORED

    account_id: AccountId
    display_name: str


@dataclass(frozen=True)
class TextContent:
    __pydantic_config__ = STORED

    text: str
    media_type: MediaType = MediaType.TEXT_PLAIN


@dataclass(frozen=True)
class StructuredContent:
    """A document a harness produced, held as the text it arrived as.

    The one place in the tree that holds JSON as a value rather than as an
    encoding: what a tool was called with, what it answered — shapes WE do not
    define and cannot type, because they belong to whichever tool it was.
    Canonicalized on construction so that two observations of one call are one
    string.
    """

    __pydantic_config__ = STORED

    json_text: str

    def __post_init__(self) -> None:
        parsed = json.loads(self.json_text)
        canonical = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        object.__setattr__(self, "json_text", canonical)

    def field(self, name: str) -> str | None:
        """One top-level string field of the document, if it has one.

        A foreign shape, read by name — a shell tool's `command`, say. Reading
        it HERE rather than at the caller is what keeps `json` inside the type
        that holds JSON: the caller asks a question about its content and gets
        a `str | None`, not a parsed document to pick through.
        """
        document = json.loads(self.json_text)
        if isinstance(document, dict) and isinstance(document.get(name), str):
            value: str = document[name]
            return value
        return None

    def readable(self) -> str:
        """The document laid out for a person to read."""
        return json.dumps(
            json.loads(self.json_text), ensure_ascii=False, indent=2, sort_keys=True
        )


Content: TypeAlias = TextContent | StructuredContent


def content_text(content: Content | None) -> str:
    """Any content as plain text — the ONE answer to that question.

    Four renderers each had their own copy of this, and all four were the same
    four lines: the dashboard's items, its snapshots, the engine's activity
    models and the terminal mirror. Four copies meant four `import json`s for a
    formatting decision that is the content type's own.
    """
    if content is None:
        return ""
    if isinstance(content, TextContent):
        return content.text
    if isinstance(content, StructuredContent):
        return content.readable()
    raise TypeError(f"unsupported content: {type(content).__name__}")


@dataclass(frozen=True)
class TokenUsage:
    __pydantic_config__ = STORED

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    one_hour_cache_write_tokens: int = 0

    def __post_init__(self) -> None:
        values = (
            self.input_tokens,
            self.output_tokens,
            self.cache_read_tokens,
            self.cache_write_tokens,
            self.one_hour_cache_write_tokens,
        )
        if any(value < 0 for value in values):
            raise ValueError("token counts cannot be negative")

    def __add__(self, token_usage: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + token_usage.input_tokens,
            output_tokens=self.output_tokens + token_usage.output_tokens,
            cache_read_tokens=self.cache_read_tokens + token_usage.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + token_usage.cache_write_tokens,
            one_hour_cache_write_tokens=(
                self.one_hour_cache_write_tokens + token_usage.one_hour_cache_write_tokens
            ),
        )


@dataclass(frozen=True)
class AttentionChoice:
    """One selectable answer. The label IS the value: both harnesses send back
    the label they were shown, so a second `value` field was a copy of the
    first that every answer gesture had to keep mapping between."""

    __pydantic_config__ = STORED

    label: str
    description: str | None = None


@dataclass(frozen=True)
class AttentionPrompt:
    __pydantic_config__ = STORED

    prompt_id: QuestionId
    title: str | None
    prompt: str
    multiple: bool
    choices: tuple[AttentionChoice, ...]


@dataclass(frozen=True)
class AttentionAnswer:
    __pydantic_config__ = STORED

    prompt_id: QuestionId
    labels: tuple[str, ...]
