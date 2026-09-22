# Copyright (c) 2026 Zhambyl Yermagambet
"""Close the public core event vocabulary with a tagged union."""

from typing import Annotated

from pydantic import Field

from baqylau_extension_api.core import actors, conversation, resources, sessions, shell, telemetry, work

type ActorPayload = (
    actors.ActorStarted | actors.ActorNameChanged | actors.ActorDescriptionChanged | actors.ActorFinished
    | actors.ActorAssignmentStarted | actors.ActorAssignmentFinished
)
type ConversationPayload = (
    conversation.TurnStarted | conversation.TurnFinished | conversation.TurnAborted | conversation.MessageCreated
    | conversation.MessageQueued | conversation.ReasoningCreated
)
type ResourcePayload = (
    resources.FileAccessed | resources.SearchPerformed | resources.SkillStarted | resources.SkillFinished
    | resources.WebFetched | resources.BrowserInteracted | resources.WorktreeChanged
)
type SessionPayload = (
    sessions.SessionStarted | sessions.SessionTitleChanged | sessions.SessionAccountChanged | sessions.SessionFinished
    | sessions.ModelChanged | sessions.EffortChanged
)
type ShellPayload = (
    shell.ShellStarted | shell.ShellProgressed | shell.ShellInputProvided | shell.ShellFinished
    | shell.ShellOutputLocated | shell.ShellBackgrounded | shell.ShellOutputFinished
)
type TelemetryPayload = (
    telemetry.UsageReported | telemetry.ContextReported | telemetry.CompactionStarted | telemetry.CompactionFinished
)
type WorkPayload = (
    work.TaskChanged | work.TaskListChanged | work.GoalChanged | work.QuestionAsked | work.QuestionAnswered
    | work.PlanProposed | work.PlanResolved
)
type CorePayload = Annotated[
    ActorPayload | ConversationPayload | ResourcePayload | SessionPayload | ShellPayload
    | TelemetryPayload | WorkPayload,
    Field(discriminator="kind"),
]
