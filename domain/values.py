"""Small immutable values used by canonical event payloads."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal, TypeAlias

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


@dataclass(frozen=True)
class ModelReference:
    native_id: str
    display_name: str | None
    selection_id: str | None


@dataclass(frozen=True)
class AccountReference:
    account_id: str
    display_name: str


@dataclass(frozen=True)
class TextContent:
    text: str
    media_type: Literal["text/plain", "text/markdown"] = "text/plain"


@dataclass(frozen=True)
class StructuredContent:
    json_text: str

    def __post_init__(self) -> None:
        parsed = json.loads(self.json_text)
        canonical = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        object.__setattr__(self, "json_text", canonical)


Content: TypeAlias = TextContent | StructuredContent


@dataclass(frozen=True)
class TokenUsage:
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
    value: str
    label: str
    description: str | None = None


@dataclass(frozen=True)
class AttentionPrompt:
    prompt_id: str
    title: str | None
    prompt: str
    multiple: bool
    choices: tuple[AttentionChoice, ...]


@dataclass(frozen=True)
class AttentionAnswer:
    prompt_id: str
    values: tuple[str, ...]
