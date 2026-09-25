# Copyright (c) 2026 Zhambyl Yermagambet
"""Describe the extension work that the engine has not done yet."""

from pydantic import BaseModel


class ExtensionWorkResponse(BaseModel):
    """Name the active owners with facts after their cursors, and count the open jobs."""

    projection_owners: tuple[str, ...]
    observer_owners: tuple[str, ...]
    open_jobs: int
    empty: bool
