"""Small immutable values used by canonical event payloads."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal, TypeAlias

from domain.stored import STORED

OperationCategory: TypeAlias = Literal[
    "shell",
    "file_read",
    "file_write",
    "file_edit",
    "search",
    "network",
    "workspace",
    "media",
    "skill",
    "task",
    "message",
    "attention",
]
Outcome: TypeAlias = Literal["succeeded", "failed", "cancelled", "rejected", "unknown"]
ActorRole: TypeAlias = Literal["lead", "child", "teammate", "sidecar"]
ExecutionMode: TypeAlias = Literal["foreground", "background", "monitor"]
# Who said it, and where the saying sits in a turn. Named because the harness
# translators BUILD both out of native JSON and are checked against these same
# lists; spelled inline on the payload they would only ever be checked at the
# constructor call.
MessageRole: TypeAlias = Literal["user", "assistant", "system", "peer", "parent"]
# `end_turn` names what the evidence says — the message a model STOPPED on, which
# every harness reports on the response itself — rather than what a reader might
# hope it means. It is deliberately NOT "the one answer of a turn": a turn that an
# injection resumes stops more than once, and each of those messages ended a
# response. A presenter that wants "the turn's final answer" derives it; the fact
# recorded here is the stop.
MessagePhase: TypeAlias = Literal["prompt", "intermediate", "end_turn", "synthetic", "recap"]
# What a file was done to, and where a goal stands. Same reason as the two
# above: the translators map a native vocabulary onto these, and the mapping
# table is the thing that has to be checked.
FileAction: TypeAlias = Literal["read", "created", "updated", "deleted", "renamed"]
# The rest of the closed sets a translator has to produce. Every one of these
# was an inline Literal on its payload, which meant the mapping that built it
# was typed `str` and checked nowhere.
AttentionType: TypeAlias = Literal["permission", "question", "plan", "confirmation"]
AttentionDecision: TypeAlias = Literal[
    "answered",
    "approved",
    "changes_requested",
    "rejected",
    "confirmed",
    "denied",
    "discussed",
]
ProgressStream: TypeAlias = Literal["output", "error", "status"]
TitleOrigin: TypeAlias = Literal["custom", "automatic", "summary"]
GoalState: TypeAlias = Literal[
    "active",
    "paused",
    "blocked",
    "usage_limited",
    "budget_limited",
    "completed",
    "cleared",
]


@dataclass(frozen=True)
class ModelReference:
    __pydantic_config__ = STORED

    native_id: str
    display_name: str | None
    selection_id: str | None


@dataclass(frozen=True)
class AccountReference:
    __pydantic_config__ = STORED

    account_id: str
    display_name: str


@dataclass(frozen=True)
class TextContent:
    __pydantic_config__ = STORED

    text: str
    media_type: Literal["text/plain", "text/markdown"] = "text/plain"


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

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            one_hour_cache_write_tokens=(
                self.one_hour_cache_write_tokens + other.one_hour_cache_write_tokens
            ),
        )


@dataclass(frozen=True)
class AttentionChoice:
    __pydantic_config__ = STORED

    value: str
    label: str
    description: str | None = None


@dataclass(frozen=True)
class AttentionPrompt:
    __pydantic_config__ = STORED

    prompt_id: str
    title: str | None
    prompt: str
    multiple: bool
    choices: tuple[AttentionChoice, ...]


@dataclass(frozen=True)
class AttentionAnswer:
    __pydantic_config__ = STORED

    prompt_id: str
    values: tuple[str, ...]
