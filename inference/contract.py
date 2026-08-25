"""The application-wide inference contract, independent of any harness."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ModelPromptRequest:
    prompt: str


@dataclass(frozen=True)
class ModelPromptResponse:
    text: str


class Model(Protocol):
    def send(self, model_prompt_request: ModelPromptRequest) -> ModelPromptResponse: ...


class ModelFactory(Protocol):
    def big(self) -> Model: ...

    def mid(self) -> Model: ...

    def small(self) -> Model: ...
