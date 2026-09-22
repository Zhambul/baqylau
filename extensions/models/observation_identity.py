# Copyright (c) 2026 Zhambyl Yermagambet
"""Allocate stable raw IDs from an owner, exact scope, source, and observation key."""

import hashlib

from baqylau_extension_api.models.base import ExtensionId, Identifier, WireModel
from baqylau_extension_api.models.observations import ObservationCandidate
from baqylau_extension_api.models.scopes import ExtensionScope

from domain.ids import RawEventId


class ObservationIdentity(WireModel):
    """Exclude runtime settings, arrival time, and database cursor from raw identity."""

    extension_id: ExtensionId
    scope: ExtensionScope
    source_identity: Identifier
    observation_key: Identifier


def observation_id(candidate: ObservationCandidate) -> RawEventId:
    """Allocate one original input identity, not a canonical fact identity.

    Returns:
        A stable ID in the host's extension observation namespace.

    """
    identity = ObservationIdentity(
        extension_id=candidate.document.schema_ref.owner, scope=candidate.scope,
        source_identity=candidate.source_identity, observation_key=candidate.observation_key,
    )
    digest = hashlib.sha256(identity.model_dump_json().encode("utf-8")).hexdigest()
    return RawEventId(f"observation:v1:{identity.extension_id}:{digest}")
