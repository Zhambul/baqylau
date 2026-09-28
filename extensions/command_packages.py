# Copyright (c) 2026 Zhambyl Yermagambet
"""Find the active package that declares a command capability."""

from baqylau_extension_api.contracts.operations import ExtensionCommands

from extensions.registry_package import RegistryPackage


class CommandNotFoundError(LookupError):
    """Reject a command whose package or declaration is not active."""


def command_package(
    packages: tuple[RegistryPackage, ...], extension_id: str,
) -> tuple[RegistryPackage, ExtensionCommands]:
    """Find the active package of one extension and its command capability.

    Returns:
        The package and its command capability.

    Raises:
        CommandNotFoundError: If no active package declares commands for the extension.

    """
    for package in packages:
        if package.manifest.extension_id != extension_id:
            continue
        plugin = package.plugin
        if plugin is not None and plugin.capabilities.commands is not None:
            return package, plugin.capabilities.commands
    message = "extension command not found"
    raise CommandNotFoundError(message)


def runtime_revision(package: RegistryPackage) -> str:
    """Read the runtime revision that the package runs in.

    Returns:
        The revision, or an empty text for a package without a runtime.

    """
    return "" if package.environment is None else package.environment.runtime_revision
