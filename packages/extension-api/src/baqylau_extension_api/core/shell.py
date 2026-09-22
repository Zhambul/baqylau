# Copyright (c) 2026 Zhambyl Yermagambet
"""Publish core shell activity without harness-specific command types."""

from typing import Literal

from baqylau_extension_api.core.base import CorePayloadModel
from baqylau_extension_api.core.content import Content
from baqylau_extension_api.core.states import ExecutionMode, Outcome, OutputMode, ProgressStream, ShellFollowUntil
from baqylau_extension_api.models.base import OpaqueId


class ShellStarted(CorePayloadModel):
    """Record a shell launch."""

    kind: Literal["shell.started"] = "shell.started"
    shell_id: OpaqueId
    command: Content
    execution: ExecutionMode
    description: str | None


class ShellProgressed(CorePayloadModel):
    """Record one ordered output change."""

    kind: Literal["shell.progressed"] = "shell.progressed"
    shell_id: OpaqueId
    ordinal: int
    stream: ProgressStream
    content: Content
    mode: OutputMode


class ShellInputProvided(CorePayloadModel):
    """Record input or an input close."""

    kind: Literal["shell.input_provided"] = "shell.input_provided"
    shell_id: OpaqueId
    content: Content | None
    closed: bool


class ShellFinished(CorePayloadModel):
    """Record the final launch outcome."""

    kind: Literal["shell.finished"] = "shell.finished"
    shell_id: OpaqueId
    outcome: Outcome
    result: Content | None
    exit_code: int | None


class ShellOutputLocated(CorePayloadModel):
    """Record an output source and its release rule."""

    kind: Literal["shell.output_located"] = "shell.output_located"
    shell_id: OpaqueId
    source_path: str
    chunk_source_type: str
    delete_source: bool
    initial_size: int
    initial_modified_at: int
    wait_for_source_change: bool
    until: ShellFollowUntil


class ShellBackgrounded(CorePayloadModel):
    """Record a foreground command that moved to the background."""

    kind: Literal["shell.backgrounded"] = "shell.backgrounded"
    shell_id: OpaqueId


class ShellOutputFinished(CorePayloadModel):
    """Record the end of a background output stream."""

    kind: Literal["shell.output_finished"] = "shell.output_finished"
    shell_id: OpaqueId
    outcome: Outcome | None = None
