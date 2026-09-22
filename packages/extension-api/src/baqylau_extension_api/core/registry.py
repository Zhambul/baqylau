# Copyright (c) 2026 Zhambyl Yermagambet
"""Select the exact public model for a known core event type."""

from collections.abc import Mapping
from types import MappingProxyType

from baqylau_extension_api.core import actors, conversation, resources, sessions, shell, telemetry, work
from baqylau_extension_api.core.base import CorePayloadModel

CORE_MODELS: Mapping[str, type[CorePayloadModel]] = MappingProxyType({
    "session.started": sessions.SessionStarted,
    "session.title_changed": sessions.SessionTitleChanged,
    "session.account_changed": sessions.SessionAccountChanged,
    "session.finished": sessions.SessionFinished,
    "model.changed": sessions.ModelChanged,
    "effort.changed": sessions.EffortChanged,
    "actor.started": actors.ActorStarted,
    "actor.name_changed": actors.ActorNameChanged,
    "actor.description_changed": actors.ActorDescriptionChanged,
    "actor.assignment_started": actors.ActorAssignmentStarted,
    "actor.assignment_finished": actors.ActorAssignmentFinished,
    "actor.finished": actors.ActorFinished,
    "turn.started": conversation.TurnStarted,
    "turn.finished": conversation.TurnFinished,
    "turn.aborted": conversation.TurnAborted,
    "message.created": conversation.MessageCreated,
    "message.queued": conversation.MessageQueued,
    "reasoning.created": conversation.ReasoningCreated,
    "shell.started": shell.ShellStarted,
    "shell.input_provided": shell.ShellInputProvided,
    "shell.progressed": shell.ShellProgressed,
    "shell.finished": shell.ShellFinished,
    "shell.output_located": shell.ShellOutputLocated,
    "shell.backgrounded": shell.ShellBackgrounded,
    "shell.output_finished": shell.ShellOutputFinished,
    "file.accessed": resources.FileAccessed,
    "search.performed": resources.SearchPerformed,
    "skill.started": resources.SkillStarted,
    "skill.finished": resources.SkillFinished,
    "web.fetched": resources.WebFetched,
    "browser.interacted": resources.BrowserInteracted,
    "worktree.changed": resources.WorktreeChanged,
    "task.changed": work.TaskChanged,
    "task.list_changed": work.TaskListChanged,
    "goal.changed": work.GoalChanged,
    "question.asked": work.QuestionAsked,
    "question.answered": work.QuestionAnswered,
    "plan.proposed": work.PlanProposed,
    "plan.resolved": work.PlanResolved,
    "usage.reported": telemetry.UsageReported,
    "context.reported": telemetry.ContextReported,
    "compaction.started": telemetry.CompactionStarted,
    "compaction.finished": telemetry.CompactionFinished,
})

CORE_SCHEMA_VERSION = 1
