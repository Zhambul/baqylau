# Copyright (c) 2026 Zhambyl Yermagambet
"""Read saved native tool details."""

from pydantic import BaseModel, ConfigDict, Field

from domain.ids import SessionId, SkillId
from harness.impl.opencode2.file_records import NativeFileChange, NativeFileInput
from harness.impl.opencode2.question_records import NativeQuestion

NATIVE_FIELDS = ConfigDict(extra="ignore")


class NativeInput(NativeFileInput):
    """Read shell input without changing other tool inputs."""

    model_config = NATIVE_FIELDS
    command: str | None = None
    background: bool = False
    description: str | None = None
    prompt: str | None = None
    questions: tuple[NativeQuestion, ...] = ()
    query: str | None = None
    url: str | None = None
    skill_id: SkillId | None = Field(default=None, alias="id")


class NativeTool(BaseModel):
    """Keep the tool name beside later tool events."""

    name: str
    input: NativeInput | None = None


class NativeContent(BaseModel):
    """Read text parts from a tool result."""

    model_config = NATIVE_FIELDS
    type: str
    text: str | None = None


class NativeMetadata(BaseModel):
    """Read the reported shell exit code."""

    model_config = NATIVE_FIELDS
    exit: int | None = None
    session_id: SessionId | None = Field(default=None, alias="sessionID")
    status: str | None = None
    answers: tuple[tuple[str, ...], ...] = ()
    files: tuple[NativeFileChange, ...] = ()


class NativeToolError(BaseModel):
    """Read the native tool failure reason."""

    model_config = NATIVE_FIELDS
    type: str
    message: str
