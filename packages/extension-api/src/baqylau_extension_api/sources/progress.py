# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep source checkpoints consistent for empty and nonempty batches."""

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.source_results import SourceBatch


def validate_progress(response: SourceBatch) -> None:
    """Reject lost progress and repeated immediate reads without advancement.

    Raises:
        ExtensionContractError: If a checkpoint erases or skips selected input.

    """
    if response.binding.after_position is not None and response.next_position is None:
        message = "source batch cannot erase committed progress"
        raise ExtensionContractError(message)
    if response.observations and response.next_position != response.observations[-1].position:
        message = "source batch must checkpoint its final observation"
        raise ExtensionContractError(message)
    if response.has_more and response.next_position == response.binding.after_position:
        message = "source batch with more work must advance its checkpoint"
        raise ExtensionContractError(message)
