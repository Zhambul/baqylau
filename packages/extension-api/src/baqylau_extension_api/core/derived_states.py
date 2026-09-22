# Copyright (c) 2026 Zhambyl Yermagambet
"""Define the closed states used by core read models and feed rows."""

from typing import Literal

LifecycleState = Literal["running", "finished"]
ActorStatus = Literal[
    "idle", "thinking", "working", "executing", "awaiting_background", "awaiting_attention", "awaiting_response",
]
RunState = Literal["succeeded", "failed", "cancelled"]
TurnState = Literal["finished", "aborted"]
FileState = Literal["succeeded", "failed"]
