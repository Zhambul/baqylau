# Copyright (c) 2026 Zhambyl Yermagambet
"""Build one package double with the command capability, and a fixed registry over package doubles."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Literal, Self, cast

from baqylau_extension_api.contracts.plugin import ExtensionCapabilities, ExtensionPlugin
from baqylau_extension_api.models import command_results, commands as sdk_commands, directory, documents, scopes
from baqylau_extension_api.models.lifecycle import ExtensionInfo

from api.extensions import command_models
from extensions.registry_package import RegistryPackage
from tests.extension_api import operation_samples, service_samples

if TYPE_CHECKING:
    from baqylau_extension_api.contracts.lifecycle import ExtensionLifecycle

OWNER = operation_samples.OWNER
COMMAND_ID = operation_samples.COMMAND_ID
SCOPE = scopes.InstallationScope()
REQUEST_KEY = "request-1"
ARGUMENTS = '"input"'


def _success(binding: sdk_commands.CommandBinding) -> command_results.CommandSucceeded:
    schema_ref = operation_samples.schema_definition().reference
    return command_results.CommandSucceeded(
        binding=binding, document=documents.EncodedDocument(schema_ref=schema_ref, json_text='"ok"'),
    )


class FakeCommands:
    """Record one execution and answer with a checked success."""

    def __init__(
        self,
        cancel_status: Literal["requested", "canceled", "not_running", "outcome_unknown"] = "canceled",
    ) -> None:
        """Start with no executed requests and one selected cancel status."""
        self.requests: list[sdk_commands.CommandRequest] = []
        self.cancel_status = cancel_status

    def execute(self, request: sdk_commands.CommandRequest) -> command_results.CommandSucceeded:
        """Record the request and return a result in the declared schema.

        Returns:
            A success with a document in the command result schema.

        """
        self.requests.append(request)
        return _success(request.binding)

    def cancel(self, request: sdk_commands.CommandCancelRequest) -> sdk_commands.CommandCancelResult:
        """Answer the selected cancellation status for the exact attempt.

        Returns:
            The checked cancellation result.

        """
        return sdk_commands.CommandCancelResult(binding=request.binding, status=self.cancel_status)

    def reconcile(self, request: sdk_commands.CommandReconcileRequest) -> command_results.CommandResult:
        """Answer one checked success for the reconciled attempt.

        Returns:
            A success with a document in the command result schema.

        """
        return _success(request.command.binding)


@dataclass(frozen=True)
class FakePlugin:
    """Carry a checked identity and the capabilities of one package double."""

    extension_info: ExtensionInfo
    capabilities: ExtensionCapabilities


def a_package(fake_commands: FakeCommands, *, effect: Literal["read", "write"] = "read") -> RegistryPackage:
    """Build an enabled package double with the command capability.

    Returns:
        The active package selection.

    """
    environment = service_samples.environment(OWNER)
    capabilities = ExtensionCapabilities(lifecycle=cast("ExtensionLifecycle", None), commands=fake_commands)
    plugin = FakePlugin(extension_info=environment.extension_info, capabilities=capabilities)
    return RegistryPackage(
        manifest=operation_samples.manifest(effect=effect),
        entry=directory.DirectoryEntry(extension_info=environment.extension_info, state="enabled"),
        environment=environment,
        plugin=cast("ExtensionPlugin", plugin),
    )


def a_request(request_key: str = REQUEST_KEY) -> command_models.ExtensionCommandRequest:
    """Build one command submission.

    Returns:
        The typed command request.

    """
    return command_models.ExtensionCommandRequest(
        scope=SCOPE.model_dump_json(), request_key=request_key, arguments=ARGUMENTS,
    )


class FakeRead:
    """Hold the selected packages and the active runtime for one registry read."""

    def __init__(self, packages: tuple[RegistryPackage, ...], runtime_revision: str) -> None:
        """Store the selected package set and its runtime revision."""
        self.snapshot = SimpleNamespace(
            packages=packages, directory=SimpleNamespace(runtime_revision=runtime_revision),
        )

    def __enter__(self) -> Self:
        """Enter the read.

        Returns:
            This read.

        """
        return self

    def __exit__(self, *args: object) -> None:
        """Leave the read."""


@dataclass(frozen=True)
class FakeRegistry:
    """Return a fixed package set for every read."""

    packages: tuple[RegistryPackage, ...]
    runtime_revision: str = "runtime-1"

    def read_snapshot(self) -> FakeRead:
        """Return one fixed read.

        Returns:
            The fixed read.

        """
        return FakeRead(self.packages, self.runtime_revision)
