# Copyright (c) 2026 Zhambyl Yermagambet
"""Name the immutable intermediate records used to check an interpretation."""

from dataclasses import dataclass

from baqylau_extension_api.models import content, events
from baqylau_extension_api.models.base import OpaqueId


@dataclass(frozen=True)
class RawTrace:
    """Keep the current derived inputs together with their exact content."""

    inputs: tuple[events.RawInput, ...]
    content_snapshot: content.ContentBundle


@dataclass(frozen=True)
class LifecycleIdentity:
    """Retain a required core fact ID and lifecycle kind in order."""

    event_id: OpaqueId
    payload_kind: str
