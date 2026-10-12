# Copyright (c) 2026 Zhambyl Yermagambet
"""Record questions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated

from pydantic import BaseModel, Field, RootModel

from harness.impl.claude_code.canonical.record_common import OPEN_FOREIGN, ForeignMetadata
from harness.impl.claude_code.ids import (
    ClaudeCodeShellId,
)


class ShellArguments(BaseModel):
    """Represent shell arguments.

    Bash / Monitor / exec_command / read_command / py / the node REPL MCP
        tool — everything TOOL_KINDS maps to `"shell"`. `command` is read by
        every one of them; `run_in_background` only by Bash (_shell_started).
    """

    model_config = OPEN_FOREIGN
    command: str | list[str] | None = None
    description: str | None = None
    run_in_background: bool | None = None
    timeout: int | float | None = None


class QuestionOption(BaseModel):
    """Represent question option."""

    model_config = OPEN_FOREIGN
    label: str | None = None
    description: str | None = None
    preview: str | None = None


class Question(BaseModel):
    """Represent question."""

    model_config = OPEN_FOREIGN
    id: str | int | None = None
    header: str | None = None
    question: str | None = None
    multi_select: Annotated[bool | None, Field(alias="multiSelect")] = None
    options: list[QuestionOption] | None = None


class QuestionAnswers(RootModel[Mapping[str, str | list[str]]]):
    """Represent question answers."""


class ToolArguments(BaseModel):
    """Declared superset of the fields read from every supported tool input."""

    model_config = OPEN_FOREIGN
    command: str | list[str] | None = None
    description: str | None = None
    run_in_background: bool | None = None
    timeout: int | float | None = None
    task_id: ClaudeCodeShellId | None = None
    file_path: str | None = None
    notebook_path: str | None = None
    content: str | None = None
    old_string: str | None = None
    new_string: str | None = None
    replace_all: bool | None = None
    limit: int | None = None
    offset: int | None = None
    pattern: str | None = None
    query: str | None = None
    max_results: int | None = None
    allowed_domains: list[str] | None = None
    url: str | None = None
    prompt: str | None = None
    name: str | None = None
    path: str | None = None
    branch: str | None = None
    action: str | None = None
    discard_changes: bool | None = None
    skill: str | None = None
    args: str | None = None
    subagent_type: str | None = None
    model: str | None = None
    team_name: str | None = None
    isolation: str | None = None
    recipient: str | None = None
    to: str | None = None
    message: str | None = None
    summary: str | None = None
    questions: list[Question] | None = None
    answers: QuestionAnswers | None = None
    annotations: ForeignMetadata | None = None
    plan: str | None = None
    plan_file_path: Annotated[str | None, Field(alias="planFilePath")] = None
