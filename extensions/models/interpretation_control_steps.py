# Copyright (c) 2026 Zhambyl Yermagambet
"""Record input that was unavailable or a call that journal admission stopped."""

from typing import Literal

from baqylau_extension_api.models.base import Digest, ExtensionId, OpaqueId, Revision, WireModel

type UnavailableInputReason = Literal["content_limit", "owner_disabled"]


class UnavailableInputStep(WireModel):
    """Record a checked preflight failure without claiming a worker call or copying large input."""

    stage: Literal["unavailable_input"] = "unavailable_input"
    reason: UnavailableInputReason
    content_byte_length: Revision
    content_digest: Digest


type LimitedCallStage = Literal["raw", "extension_translation", "canonical"]


class LimitStep(WireModel):
    """Record one selected worker call that journal admission did not allow."""

    stage: Literal["limit"] = "limit"
    reason: Literal["journal_limit"] = "journal_limit"
    call_stage: LimitedCallStage
    extension_id: ExtensionId
    input_ids: tuple[OpaqueId, ...] = ()
