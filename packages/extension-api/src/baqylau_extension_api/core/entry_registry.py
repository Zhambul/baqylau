# Copyright (c) 2026 Zhambyl Yermagambet
"""Select the exact public model for each current core feed body."""

from collections.abc import Mapping
from types import MappingProxyType

from baqylau_extension_api.core import (
    entry_attention,
    entry_conversation,
    entry_extensions,
    entry_lifecycle,
    entry_resources,
    entry_shells,
)
from baqylau_extension_api.core.entry_base import CoreEntryBodyModel

CORE_ENTRY_MODELS: Mapping[str, type[CoreEntryBodyModel]] = MappingProxyType({
    "turn_started": entry_conversation.TurnStartedBody,
    "turn_finished": entry_conversation.TurnFinishedBody,
    "message": entry_conversation.MessageBody,
    "reasoning": entry_conversation.ReasoningBody,
    "shell_started": entry_shells.ShellStartedBody,
    "shell_output": entry_shells.ShellOutputBody,
    "shell_backgrounded": entry_shells.ShellBackgroundedBody,
    "shell_finished": entry_shells.ShellFinishedBody,
    "file": entry_resources.FileBody,
    "search": entry_resources.SearchBody,
    "web": entry_resources.WebBody,
    "browser": entry_resources.BrowserBody,
    "worktree": entry_resources.WorktreeBody,
    "skill_started": entry_attention.SkillStartedBody,
    "skill_finished": entry_attention.SkillFinishedBody,
    "question_asked": entry_attention.QuestionAskedBody,
    "question_answered": entry_attention.QuestionAnsweredBody,
    "plan_proposed": entry_attention.PlanProposedBody,
    "plan_resolved": entry_attention.PlanResolvedBody,
    "compaction_started": entry_lifecycle.CompactionStartedBody,
    "compaction_finished": entry_lifecycle.CompactionFinishedBody,
    "assignment_started": entry_lifecycle.AssignmentStartedBody,
    "assignment_finished": entry_lifecycle.AssignmentFinishedBody,
    "model_change": entry_lifecycle.ModelChangeBody,
    "effort_change": entry_lifecycle.EffortChangeBody,
    "extension": entry_extensions.ExtensionBody,
})

CORE_PROJECTION_SCHEMA_VERSION = 1
