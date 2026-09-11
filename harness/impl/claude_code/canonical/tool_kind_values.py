# Copyright (c) 2026 Zhambyl Yermagambet
"""Define Claude Code tool kinds."""

from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING

from domain.outcomes import FileAction

if TYPE_CHECKING:
    from collections.abc import Mapping


class ToolKind(StrEnum):
    """Identify the canonical kind of a native tool."""

    SHELL = "shell"
    FILE = "file"
    SEARCH = "search"
    WEB = "web"
    BROWSER = "browser"
    WORKTREE = "worktree"
    SKILL = "skill"
    ASSIGNMENT = "assignment"
    MESSAGE = "message"
    QUESTION = "question"
    PLAN = "plan"
    IGNORED = "ignored"


MONITOR_TOOL_NAME = "Monitor"


TOOL_KINDS: Mapping[str, ToolKind] = MappingProxyType({
    "Bash": ToolKind.SHELL,
    MONITOR_TOOL_NAME: ToolKind.SHELL,
    "exec_command": ToolKind.SHELL,
    "read_command": ToolKind.SHELL,
    "py": ToolKind.SHELL,
    "mcp__node_repl__js": ToolKind.SHELL,
    "Read": ToolKind.FILE,
    "Write": ToolKind.FILE,
    "Edit": ToolKind.FILE,
    "MultiEdit": ToolKind.FILE,
    "NotebookEdit": ToolKind.FILE,
    "Grep": ToolKind.SEARCH,
    "Glob": ToolKind.SEARCH,
    "WebSearch": ToolKind.SEARCH,
    "ToolSearch": ToolKind.SEARCH,
    "WebFetch": ToolKind.WEB,
    "EnterWorktree": ToolKind.WORKTREE,
    "ExitWorktree": ToolKind.WORKTREE,
    "Skill": ToolKind.SKILL,
    "Task": ToolKind.ASSIGNMENT,
    "Agent": ToolKind.ASSIGNMENT,
    "SendMessage": ToolKind.MESSAGE,
    "AskUserQuestion": ToolKind.QUESTION,
    "ExitPlanMode": ToolKind.PLAN,
    "EnterPlanMode": ToolKind.IGNORED,
    "TaskCreate": ToolKind.IGNORED,
    "TaskUpdate": ToolKind.IGNORED,
    "TaskGet": ToolKind.IGNORED,
    "TaskList": ToolKind.IGNORED,
    "TaskStop": ToolKind.IGNORED,
    "TaskOutput": ToolKind.IGNORED,
    "ListAgents": ToolKind.IGNORED,
    "DesignSync": ToolKind.IGNORED,
    "GenerateImage": ToolKind.IGNORED,
    "image_gen__imagegen": ToolKind.IGNORED,
})


SEARCH_QUERY_FIELDS = ("pattern", "query")


TRANSCRIPT_RESULT_KINDS = frozenset(
    (
        ToolKind.QUESTION,
        ToolKind.SHELL,
        ToolKind.FILE,
        ToolKind.SEARCH,
        ToolKind.WEB,
        ToolKind.BROWSER,
        ToolKind.WORKTREE,
        ToolKind.SKILL,
    ),
)


FILE_ACTIONS: Mapping[str, FileAction] = MappingProxyType({
    "Read": FileAction.READ,
    "Write": FileAction.CREATED,
    "Edit": FileAction.UPDATED,
    "MultiEdit": FileAction.UPDATED,
    "NotebookEdit": FileAction.UPDATED,
})
