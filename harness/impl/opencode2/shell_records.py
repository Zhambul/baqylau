# Copyright (c) 2026 Zhambyl Yermagambet
"""Read the native shell identity saved beside a tool call."""

from pydantic import BaseModel, ConfigDict

from domain.ids import ShellId


class NativeShell(BaseModel):
    """Keep the output path and original call identity across turn completion."""

    model_config = ConfigDict(extra="ignore")
    call_id: ShellId
    file: str
    status: str
    exit: int | None = None
