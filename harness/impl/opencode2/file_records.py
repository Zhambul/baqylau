# Copyright (c) 2026 Zhambyl Yermagambet
"""Read native file input and saved changes."""

from pydantic import BaseModel, ConfigDict

NATIVE_FIELDS = ConfigDict(extra="ignore")


class NativeFileInput(BaseModel):
    """Read the path and content supplied to a native file tool."""

    model_config = NATIVE_FIELDS
    path: str | None = None
    content: str | None = None


class NativeFileChange(BaseModel):
    """Read the diff and counts supplied by a completed edit."""

    model_config = NATIVE_FIELDS
    file: str
    patch: str | None = None
    status: str
    additions: int | None = None
    deletions: int | None = None
