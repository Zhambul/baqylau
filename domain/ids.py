"""Opaque canonical identities and deterministic identity construction."""

from __future__ import annotations

import hashlib
from typing import NewType

SessionId = NewType("SessionId", str)
ActorId = NewType("ActorId", str)
TurnId = NewType("TurnId", str)
RawEventId = NewType("RawEventId", str)
CanonicalEventId = NewType("CanonicalEventId", str)
MessageId = NewType("MessageId", str)
ShellId = NewType("ShellId", str)
SkillId = NewType("SkillId", str)
AssignmentId = NewType("AssignmentId", str)
TaskId = NewType("TaskId", str)
AttentionId = NewType("AttentionId", str)


def stable_event_id(
    *,
    harness: str,
    session_id: SessionId,
    actor_id: ActorId,
    subject_type: str,
    subject_id: str,
    phase: str,
) -> CanonicalEventId:
    """Build the same event identity for every observation of one native fact."""
    identity = "\x1f".join(
        (harness, str(session_id), str(actor_id), subject_type, subject_id, phase)
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return CanonicalEventId(f"{harness}:{subject_type}:{digest}")
