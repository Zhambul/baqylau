# Copyright (c) 2026 Zhambyl Yermagambet
"""Opaque canonical identities and deterministic identity construction."""

import hashlib
from dataclasses import dataclass
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
WindowId = NewType("WindowId", str)
TabId = NewType("TabId", str)
AccountId = NewType("AccountId", str)
DeviceId = NewType("DeviceId", str)
UploadId = NewType("UploadId", str)
RequestId = NewType("RequestId", str)
ReasoningId = NewType("ReasoningId", str)
ClientId = NewType("ClientId", str)
TaskListId = NewType("TaskListId", str)
QuestionId = NewType("QuestionId", str)


HarnessName = NewType("HarnessName", str)


@dataclass(frozen=True, slots=True)
class CanonicalEventIdentity:
    """Hold the native fields that identify one canonical event."""

    harness: HarnessName
    session_id: SessionId
    actor_id: ActorId
    subject_type: str
    subject_id: str
    phase: str


def stable_event_id(canonical_event_identity: CanonicalEventIdentity) -> CanonicalEventId:
    """Build the same event identity for every observation of one native fact.

    Returns:
        The canonical event id.

    """
    identity_text = "\x1f".join(
        (
            canonical_event_identity.harness,
            str(canonical_event_identity.session_id),
            str(canonical_event_identity.actor_id),
            canonical_event_identity.subject_type,
            canonical_event_identity.subject_id,
            canonical_event_identity.phase,
        ),
    )
    digest = hashlib.sha256(identity_text.encode()).hexdigest()
    return CanonicalEventId(
        f"{canonical_event_identity.harness}:{canonical_event_identity.subject_type}:{digest}",
    )
