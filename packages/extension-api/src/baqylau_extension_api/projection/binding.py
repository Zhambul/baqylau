# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep canonical progress distinct from derived-data snapshot revisions."""

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.projections import ProjectionBinding


def validate_binding(binding: ProjectionBinding) -> None:
    """Require one scope and history without mixing the two cursor domains.

    Raises:
        ExtensionContractError: If context and prior boundary do not agree.

    """
    context = binding.context
    snapshot = binding.snapshot
    if context.scope != snapshot.scope or context.history_revision != snapshot.history_revision:
        message = "projection snapshot must have the context scope and history"
        raise ExtensionContractError(message)
    if binding.after_input_cursor > context.input_cursor:
        message = "projection input boundary cannot move backwards"
        raise ExtensionContractError(message)
