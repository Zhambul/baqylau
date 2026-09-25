# Copyright (c) 2026 Zhambyl Yermagambet
"""Declare the beta package, which exposes one public command through its service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from baqylau_extension_api.contracts.plugin import ExtensionCapabilities, ExtensionPlugin
from baqylau_extension_api.manifest.operations import CommandDefinition, PublicService
from baqylau_extension_api.models import command_results, commands, directory, documents, scopes, service_jobs, services

from extensions.registry_package import RegistryPackage
from tests.extension_api import samples, service_samples as peers
from tests.extension_api.example import SampleLifecycle

WRITE_COMMAND = f"{peers.BETA}.write"
SCOPE = scopes.InstallationScope()
BETA_DOCUMENT = documents.EncodedDocument(schema_ref=peers.schema(peers.BETA).reference, json_text='"ok"')


class BetaCommands:
    """Answer beta's public command with a document in beta's schema."""

    def execute(self, request: commands.CommandRequest) -> command_results.CommandResult:
        """Return one checked success.

        Returns:
            A success in beta's result schema.

        """
        return command_results.CommandSucceeded(binding=request.binding, document=BETA_DOCUMENT)

    def cancel(self, request: commands.CommandCancelRequest) -> commands.CommandCancelResult:
        """Report a proven stop.

        Returns:
            The canceled acknowledgment.

        """
        return commands.CommandCancelResult(binding=request.binding, status="canceled")

    def reconcile(self, request: commands.CommandReconcileRequest) -> command_results.CommandResult:
        """Resolve the attempt with the same success.

        Returns:
            A success in beta's result schema.

        """
        return command_results.CommandSucceeded(binding=request.command.binding, document=BETA_DOCUMENT)


@dataclass(frozen=True)
class BetaPlugin:
    """Carry beta's identity and its command capability."""

    extension_info: object
    capabilities: ExtensionCapabilities


def beta_package(effect: Literal["read", "write"] = "read") -> RegistryPackage:
    """Declare beta with one public command and no other capability.

    Returns:
        An enabled beta selection.

    """
    environment = peers.environment(peers.BETA)
    reference = peers.schema(peers.BETA).reference
    original = peers.manifest(peers.BETA)
    manifest = original.model_copy(update={
        "capabilities": ("lifecycle", "commands"),
        "contributions": original.contributions.model_copy(update={
            "queries": (),
            "processing": (),
            "commands": (CommandDefinition(
                name=WRITE_COMMAND, scopes=("installation",), arguments=reference, result=reference,
                effect=effect, reconciliation=True,
            ),),
            "services": (PublicService(name=f"{peers.BETA}.service", version="1.0.0", commands=(WRITE_COMMAND,)),),
        }),
    })
    plugin = BetaPlugin(
        extension_info=environment.extension_info,
        capabilities=ExtensionCapabilities(lifecycle=SampleLifecycle(), commands=BetaCommands()),
    )
    return RegistryPackage(
        manifest=manifest,
        entry=directory.DirectoryEntry(extension_info=environment.extension_info, state="enabled"),
        environment=environment,
        plugin=cast("ExtensionPlugin", plugin),
    )


def a_command(command_id: str = WRITE_COMMAND, request_key: str = "peer-key") -> service_jobs.ServiceCommandRequest:
    """Ask beta's service to accept its public command.

    Returns:
        The peer command request.

    """
    return service_jobs.ServiceCommandRequest(
        binding=peers.resolve_request().binding,
        service_revision=services.ServiceRevision(
            package_version="1.0.0", service_version="1.0.0", runtime_revision=samples.RUNTIME_REVISION,
        ),
        command_id=command_id,
        arguments=BETA_DOCUMENT,
        request_key=request_key,
        expected_state_revision="state-1",
    )
