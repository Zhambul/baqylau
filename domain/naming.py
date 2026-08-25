"""Durable automatic-title jobs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from domain.ids import SessionId


class NamingJobState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class NamingJob:
    key: str
    session_id: SessionId
    prompt: str
    state: NamingJobState = NamingJobState.PENDING
    title: str | None = None
    error: str | None = None
