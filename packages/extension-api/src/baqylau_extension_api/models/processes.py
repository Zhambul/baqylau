# Copyright (c) 2026 Zhambyl Yermagambet
"""Ask the host to run one declared program with an argument array; no shell runs."""

from typing import Annotated, Literal

from pydantic import Field

from baqylau_extension_api.models.base import Identifier, WireModel
from baqylau_extension_api.paths import AbsolutePath

MAX_PROCESS_ARGUMENTS = 1000
MAX_ARGUMENT_LENGTH = 65_536
MAX_PROCESS_OUTPUT = 1_048_576

type ProcessArgument = Annotated[str, Field(max_length=MAX_ARGUMENT_LENGTH)]


class ProcessRequest(WireModel):
    """Select one declared program, its arguments, its working directory, and an optional shorter deadline."""

    name: Identifier
    arguments: Annotated[tuple[ProcessArgument, ...], Field(max_length=MAX_PROCESS_ARGUMENTS)] = ()
    cwd: AbsolutePath
    timeout_seconds: Annotated[float, Field(gt=0)] | None = None


class ProcessExited(WireModel):
    """Return the exit code and the complete bounded output of a finished program."""

    status: Literal["exited"] = "exited"
    name: Identifier
    exit_code: int
    stdout: Annotated[str, Field(max_length=MAX_PROCESS_OUTPUT)]
    stderr: Annotated[str, Field(max_length=MAX_PROCESS_OUTPUT)]


class ProcessFailed(WireModel):
    """Report a program that did not finish inside its bounds, or was not found."""

    status: Literal["failed"] = "failed"
    name: Identifier
    reason: Literal["timed_out", "output_limit", "not_found"]


type ProcessResult = Annotated[ProcessExited | ProcessFailed, Field(discriminator="status")]
