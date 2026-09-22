# Copyright (c) 2026 Zhambyl Yermagambet
"""Capture the exact original bytes and source identity for a processing trace."""

from baqylau_extension_api.models import content, events, scopes
from baqylau_extension_api.models.base import WireModel

from extensions.models.observations import ExtensionObservation, StoredObservation


class CapturedOriginal(WireModel):
    """Use one immutable source and its supplied content, without a live file read."""

    source: events.RawInput
    content_snapshot: content.ContentBundle


def capture_original(stored: StoredObservation) -> CapturedOriginal:
    """Map either stored observation branch to the existing public raw contract.

    Returns:
        The complete original input with a stable input ID and exact content.

    """
    original = stored.observation
    if isinstance(original, ExtensionObservation):
        candidate = original.candidate
        blob = content.encode_content(candidate.document.json_text.encode("utf-8"), "application/json")
        source = events.RawInput(
            input_id=original.raw_event_id, scope=candidate.scope, source_type=candidate.source_type,
            source=events.SourceReference(
                raw_event_id=original.raw_event_id, source_identity=candidate.source_identity,
                source_position=original.source_position,
            ), content=blob.reference, origin="extension", owner=candidate.document.schema_ref.owner,
        )
    else:
        blob = content.encode_content(
            original.payload, "application/json" if original.encoding == "json" else "text/plain",
        )
        source = events.RawInput(
            input_id=original.raw_event_id,
            scope=observation_scope(stored),
            source_type=original.source_type,
            source=events.SourceReference(
                raw_event_id=original.raw_event_id, source_identity=original.source_identity,
                source_position=original.source_position,
            ), content=blob.reference, origin="harness", owner=original.harness,
        )
    return CapturedOriginal(source=source, content_snapshot=content.ContentBundle(blobs=(blob,)))


def observation_scope(stored: StoredObservation) -> scopes.ExtensionScope:
    """Read the exact scope without encoding a second content copy.

    Returns:
        The stored extension scope or strict core session scope.

    """
    original = stored.observation
    if isinstance(original, ExtensionObservation):
        return original.candidate.scope
    return scopes.SessionScope(session_id=original.session_id, actor_id=original.actor_id, harness=original.harness)


def original_bytes(stored: StoredObservation) -> bytes:
    """Read the exact recorded content without a bounded worker encoding.

    Returns:
        Original bytes, including content that exceeds the transfer limit.

    """
    original = stored.observation
    if isinstance(original, ExtensionObservation):
        return original.candidate.document.json_text.encode("utf-8")
    return original.payload
