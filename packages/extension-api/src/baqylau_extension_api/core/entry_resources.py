# Copyright (c) 2026 Zhambyl Yermagambet
"""Publish typed core file, search, web, browser, and worktree feed bodies."""

from typing import Literal

from baqylau_extension_api.core.content import Content
from baqylau_extension_api.core.derived_states import FileState
from baqylau_extension_api.core.entry_base import CoreEntryBodyModel
from baqylau_extension_api.core.states import FileAction, WorktreeAction


class FileBody(CoreEntryBodyModel):
    """Keep file operation state and optional source content."""

    kind: Literal["file"] = "file"
    path: str
    action: FileAction
    state: FileState
    previous_path: str | None = None
    lines_added: int | None = None
    lines_removed: int | None = None
    content: Content | None = None
    line_start: int | None = None
    line_end: int | None = None


class SearchBody(CoreEntryBodyModel):
    """Keep a search request and optional result."""

    kind: Literal["search"] = "search"
    tool: str
    query: Content
    state: FileState
    result: Content | None = None


class WebBody(CoreEntryBodyModel):
    """Keep a web fetch and its result state."""

    kind: Literal["web"] = "web"
    url: str | None
    state: FileState
    result: Content | None = None


class BrowserBody(CoreEntryBodyModel):
    """Keep a browser action and its result state."""

    kind: Literal["browser"] = "browser"
    action: str
    state: FileState
    result: Content | None = None


class WorktreeBody(CoreEntryBodyModel):
    """Keep a worktree transition and its result state."""

    kind: Literal["worktree"] = "worktree"
    action: WorktreeAction
    state: FileState
    arguments: Content | None = None
