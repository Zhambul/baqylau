# Copyright (c) 2026 Zhambyl Yermagambet
"""Require declared, schema-valid terminal actions with explicit state."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.validation import validate_manifest
from baqylau_extension_api.schemas import SchemaSet
from baqylau_extension_api.terminal.actions import validate_presentation_actions
from baqylau_extension_api.terminal.validation import validate_terminal_view

from tests.extension_api import terminal_samples

ACTIONS_FIELD = "actions"


def test_action_uses_a_registered_command() -> None:
    """Accept a typed action without executing it or accessing a repository."""
    manifest = validate_manifest(terminal_samples.command_manifest())
    response = validate_terminal_view(terminal_samples.view_request(), terminal_samples.action_response())
    validate_presentation_actions(manifest, SchemaSet(manifest.schemas), response)


@pytest.mark.parametrize("change", [{"command_id": "test.sample.absent"}, {"expected_state_revision": None}])
def test_terminal_action_rejects_invalid_command(change: dict[str, object]) -> None:
    """Reject unknown commands and write requests without expected state."""
    manifest = terminal_samples.command_manifest()
    response = terminal_samples.action_response()
    action = response.actions[0].model_copy(update=change)
    changed = response.model_copy(update={ACTIONS_FIELD: (action,)})
    with pytest.raises(ExtensionContractError):
        validate_presentation_actions(manifest, SchemaSet(manifest.schemas), changed)


def test_action_requires_its_declared_scope() -> None:
    """Do not offer a session-only command in a repository or installation view."""
    manifest = terminal_samples.command_manifest()
    command = manifest.contributions.commands[0].model_copy(update={"scopes": ("session",)})
    changed = manifest.model_copy(update={
        "contributions": manifest.contributions.model_copy(update={"commands": (command,)}),
    })
    with pytest.raises(ExtensionContractError, match="scope is not declared"):
        validate_presentation_actions(changed, SchemaSet(manifest.schemas), terminal_samples.action_response())


def test_terminal_action_checks_argument_content() -> None:
    """Keep invalid encoded data from reaching a registered command."""
    manifest = terminal_samples.command_manifest()
    response = terminal_samples.action_response()
    action = response.actions[0]
    arguments = action.arguments.model_copy(update={"json_text": "42"})
    action = action.model_copy(update={"arguments": arguments})
    with pytest.raises(ExtensionContractError):
        validate_presentation_actions(manifest, SchemaSet(manifest.schemas), response.model_copy(update={
            ACTIONS_FIELD: (action,),
        }))


@pytest.mark.parametrize("change", [
    {"command_id": "peer.write"},
    {"arguments": terminal_samples.action_response().actions[0].arguments.model_copy(update={
        "schema_ref": terminal_samples.schema_definition().reference.model_copy(update={"owner": "peer"}),
    })},
])
def test_terminal_action_cannot_claim_a_peer(change: dict[str, object]) -> None:
    """Require a package-owned command and argument schema."""
    response = terminal_samples.action_response()
    action = response.actions[0].model_copy(update=change)
    with pytest.raises(ExtensionContractError):
        validate_terminal_view(terminal_samples.view_request(), response.model_copy(update={ACTIONS_FIELD: (action,)}))


def test_terminal_action_ids_are_unique() -> None:
    """Do not let input dispatch select between two actions with one ID."""
    response = terminal_samples.action_response()
    with pytest.raises(ExtensionContractError, match="action IDs must be unique"):
        validate_terminal_view(terminal_samples.view_request(), response.model_copy(update={
            ACTIONS_FIELD: response.actions * 2,
        }))
