# Copyright (c) 2026 Zhambyl Yermagambet
"""Publish core file, search, web, and skill payloads."""

from typing import Literal

from baqylau_extension_api.core.base import CorePayloadModel
from baqylau_extension_api.core.content import Content
from baqylau_extension_api.core.states import FileAction, Outcome, WorktreeAction
from baqylau_extension_api.models.base import OpaqueId


class FileAccessed(CorePayloadModel):
    """Record a file operation and its available content or diff."""

    kind: Literal["file.accessed"] = "file.accessed"
    path: str
    action: FileAction
    outcome: Outcome
    previous_path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    lines_added: int | None = None
    lines_removed: int | None = None
    unified_diff: str | None = None
    content: Content | None = None


class SearchPerformed(CorePayloadModel):
    """Record a search and its result."""

    kind: Literal["search.performed"] = "search.performed"
    tool: str
    query: Content
    result: Content | None
    outcome: Outcome


class SkillStarted(CorePayloadModel):
    """Record a skill call and its arguments."""

    kind: Literal["skill.started"] = "skill.started"
    skill_id: OpaqueId
    name: str
    arguments: Content | None


class SkillFinished(CorePayloadModel):
    """Record a skill call's final outcome."""

    kind: Literal["skill.finished"] = "skill.finished"
    skill_id: OpaqueId
    outcome: Outcome
    result: Content | None


class WebFetched(CorePayloadModel):
    """Record a fetched page and its outcome."""

    kind: Literal["web.fetched"] = "web.fetched"
    url: str | None
    result: Content | None
    outcome: Outcome


class BrowserInteracted(CorePayloadModel):
    """Record a browser action and observation."""

    kind: Literal["browser.interacted"] = "browser.interacted"
    action: str
    result: Content | None
    outcome: Outcome


class WorktreeChanged(CorePayloadModel):
    """Record entry to or exit from a worktree."""

    kind: Literal["worktree.changed"] = "worktree.changed"
    action: WorktreeAction
    arguments: Content | None
    outcome: Outcome
