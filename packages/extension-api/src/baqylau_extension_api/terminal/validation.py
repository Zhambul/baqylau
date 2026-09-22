# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate complete terminal results before they reach a client."""

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest import rules
from baqylau_extension_api.terminal.layout_rules import validate_layout
from baqylau_extension_api.terminal.models import TerminalView, TerminalViewRequest

MAX_TERMINAL_VIEW_BYTES = 1_048_576


def validate_terminal_view(request: TerminalViewRequest, response: TerminalView) -> TerminalView:
    """Reject stale identity, invalid layout, and excessive output atomically.

    Returns:
        A revalidated response with the same binding as the request.

    Raises:
        ExtensionContractError: If ownership, identity, or layout is invalid.

    """
    checked_request = TerminalViewRequest.model_validate(request)
    checked = TerminalView.model_validate(response)
    if checked.binding != checked_request.binding:
        message = "terminal response does not match its requested view revision"
        raise ExtensionContractError(message)
    _validate_identity(checked)
    validate_layout(checked.blocks, frozenset(action.action_id for action in checked.actions))
    if len(checked.model_dump_json().encode("utf-8")) > MAX_TERMINAL_VIEW_BYTES:
        message = "terminal response exceeds its encoded size limit"
        raise ExtensionContractError(message)
    return checked


def _validate_identity(response: TerminalView) -> None:
    owner = response.binding.extension_id
    rules.require_owned((response.binding.view_id,), owner)
    rules.require_owned((action.command_id for action in response.actions), owner)
    rules.require_unique((action.action_id for action in response.actions), "terminal action IDs")
    for action in response.actions:
        if action.arguments.schema_ref.owner != owner:
            message = "terminal action arguments must use the extension's schema"
            raise ExtensionContractError(message)
