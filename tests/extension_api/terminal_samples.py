# Copyright (c) 2026 Zhambyl Yermagambet
"""Build fixed terminal requests and package declarations for tests."""

from baqylau_extension_api.manifest.contributions import Contributions
from baqylau_extension_api.manifest.metadata import BackendEntry
from baqylau_extension_api.manifest.operations import CommandDefinition
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.manifest.views import TerminalView as TerminalContribution
from baqylau_extension_api.models.documents import EncodedDocument, SchemaDefinition
from baqylau_extension_api.models.scopes import InstallationScope, SnapshotCursor
from baqylau_extension_api.runtime.worker_models import WorkerLoadRequest
from baqylau_extension_api.terminal.models import (
    TerminalAction,
    TerminalBinding,
    TerminalView,
    TerminalViewport,
    TerminalViewRequest,
)

from tests.extension_api import manifest_samples, samples

VIEW_WIDTH = 80
OWNER = "test.sample"
VIEW_ID = f"{OWNER}.terminal"
COMMAND_ID = f"{OWNER}.write"


def load_request() -> WorkerLoadRequest:
    """Register only the terminal fixture's declared capability.

    Returns:
        A validated-shape package request without feature imports.

    """
    manifest = manifest_samples.backend_manifest(OWNER).model_copy(update={
        "backend": BackendEntry(module="terminal_backend"),
        "capabilities": ("lifecycle", "terminal"),
        "contributions": Contributions(terminal=(TerminalContribution(
            view_id=VIEW_ID, title="Example", scopes=("installation",), pane="example",
        ),)),
    })
    return WorkerLoadRequest(manifest=manifest, environment=samples.worker_environment())


def view_request() -> TerminalViewRequest:
    """Select one installation-scoped view without a fake coding session.

    Returns:
        A request bound to recorded data, settings, and runtime revisions.

    """
    return TerminalViewRequest(
        binding=TerminalBinding(
            extension_id=OWNER, view_id=VIEW_ID,
            runtime_revision=samples.RUNTIME_REVISION, settings_revision=0,
            snapshot=SnapshotCursor(
                scope=InstallationScope(), history_revision="history-1", projection_generation="projection-1",
                commit_cursor=1,
            ),
        ), viewport=TerminalViewport(columns=VIEW_WIDTH, rows=24),
    )


def schema_definition() -> SchemaDefinition:
    """Own a small text schema in the terminal fixture's namespace.

    Returns:
        The exact same schema bytes with an independent owner identity.

    """
    schema = samples.schema_definition()
    reference = schema.reference.model_copy(update={"owner": OWNER})
    return schema.model_copy(update={"reference": reference})


def action_response() -> TerminalView:
    """Declare a data-only write action without executing any command.

    Returns:
        A response with a typed command reference and expected state.

    """
    document = EncodedDocument(schema_ref=schema_definition().reference, json_text='"message"')
    action = TerminalAction(
        action_id="write", command_id=COMMAND_ID, label="Write", arguments=document,
        expected_state_revision="state-1",
    )
    return TerminalView(binding=view_request().binding, title="Actions", blocks=(), actions=(action,))


def command_manifest() -> ExtensionManifest:
    """Describe a command for pure action registration tests only.

    Returns:
        A data-only manifest; the terminal worker does not execute this command.

    """
    manifest = load_request().manifest
    schema = schema_definition()
    command = CommandDefinition(
        name=COMMAND_ID, scopes=("installation",), arguments=schema.reference, result=schema.reference,
        effect="write", reconciliation=True,
    )
    return manifest.model_copy(update={
        "schemas": (schema,), "capabilities": ("lifecycle", "terminal", "commands"),
        "contributions": manifest.contributions.model_copy(update={"commands": (command,)}),
    })
