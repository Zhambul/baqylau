# Copyright (c) 2026 Zhambyl Yermagambet
"""Read native question choices and submitted answer drafts."""

from pydantic import BaseModel, ConfigDict


class NativeChoice(BaseModel):
    """Read one native choice."""

    label: str
    description: str | None = None


class NativeQuestion(BaseModel):
    """Read the native question tool input."""

    model_config = ConfigDict(extra="ignore")
    question: str
    header: str | None = None
    multiple: bool = False
    options: tuple[NativeChoice, ...] = ()


class AnswerDraft(BaseModel):
    """Read the common dashboard answer format."""

    model_config = ConfigDict(extra="forbid")
    selected: tuple[str, ...] = ()
    other: str | None = None
