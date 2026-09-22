# Copyright (c) 2026 Zhambyl Yermagambet
"""Publish typed core shell feed bodies."""

from typing import Literal

from baqylau_extension_api.core.content import Content
from baqylau_extension_api.core.derived_states import RunState
from baqylau_extension_api.core.entry_base import CoreEntryBodyModel
from baqylau_extension_api.core.states import ExecutionMode, OutputMode, ProgressStream
from baqylau_extension_api.models.base import OpaqueId


class ShellStartedBody(CoreEntryBodyModel):
    """Keep the original command and execution mode."""

    kind: Literal["shell_started"] = "shell_started"
    shell_id: OpaqueId
    command: Content
    execution: ExecutionMode


class ShellOutputBody(CoreEntryBodyModel):
    """Keep one exact output chunk with stream and update mode."""

    kind: Literal["shell_output"] = "shell_output"
    shell_id: OpaqueId
    stream: ProgressStream
    mode: OutputMode
    content: Content


class ShellBackgroundedBody(CoreEntryBodyModel):
    """Keep the shell reference when work moves to the background."""

    kind: Literal["shell_backgrounded"] = "shell_backgrounded"
    shell_id: OpaqueId


class ShellFinishedBody(CoreEntryBodyModel):
    """Keep final shell state, exit code, and optional result."""

    kind: Literal["shell_finished"] = "shell_finished"
    shell_id: OpaqueId
    state: RunState
    exit_code: int | None = None
    result: Content | None = None
