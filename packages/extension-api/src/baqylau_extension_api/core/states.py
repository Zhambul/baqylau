# Copyright (c) 2026 Zhambyl Yermagambet
"""Define closed public states for core events."""

from typing import Literal

Outcome = Literal["succeeded", "failed", "cancelled", "rejected", "unknown"]
ExecutionMode = Literal["foreground", "background", "monitor"]
FileAction = Literal["read", "created", "updated", "deleted", "renamed"]
PlanState = Literal["approved", "changes_requested", "rejected"]
WorktreeAction = Literal["entered", "exited"]
ProgressStream = Literal["output", "error", "status"]
OutputMode = Literal["append", "replace"]
ModelChangeReason = Literal["selected", "automatic_fallback", "reported_by_harness"]
EffortChangeReason = Literal["selected", "reported_by_harness"]
TitleOrigin = Literal["custom", "automatic", "summary"]
GoalState = Literal["active", "paused", "blocked", "usage_limited", "budget_limited", "completed", "cleared"]
ShellFollowUntil = Literal["shell_finished", "session_finished"]
TaskState = Literal["pending", "in_progress", "completed", "deleted"]
ActorRole = Literal["lead", "child", "teammate", "sidecar"]
MessageRole = Literal["user", "assistant", "system", "peer", "parent"]
MessagePhase = Literal["prompt", "intermediate", "end_turn", "synthetic", "recap"]
UsageScope = Literal["session", "actor", "turn", "operation"]
