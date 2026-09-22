# Copyright (c) 2026 Zhambyl Yermagambet
"""Check declared consumer access and current provider versions before dispatch."""

from packaging.specifiers import SpecifierSet

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.operations import ServiceRequirement
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models.services import ServiceBinding, ServiceResolved, ServiceRevision, ServiceUnavailable
from baqylau_extension_api.runtime.service_provider import ServiceProvider


def resolve_provider(
    consumer: ExtensionManifest, binding: ServiceBinding, provider: ServiceProvider | None,
) -> ServiceResolved | ServiceUnavailable:
    """Resolve a declared service with no feature execution or settings disclosure.

    Returns:
        Current read metadata or a typed missing, inactive, or incompatible result.

    """
    requirement = require_service_consumer(consumer, binding)
    if provider is None:
        return ServiceUnavailable(binding=binding, reason="not_installed")
    if provider.environment is None:
        return ServiceUnavailable(binding=binding, reason="not_enabled")
    _require_identity(binding, provider)
    if not _package_compatible(consumer, provider):
        return ServiceUnavailable(binding=binding, reason="incompatible")
    return _resolve_service(requirement, binding, provider)


def require_service_consumer(consumer: ExtensionManifest, binding: ServiceBinding) -> ServiceRequirement:
    """Reject undeclared access before reading any peer settings or runtime data.

    Returns:
        The exact service requirement declared by this package.

    Raises:
        ExtensionContractError: If this consumer did not declare the selected service.

    """
    for requirement in consumer.contributions.consumes:
        if requirement.owner == binding.owner and requirement.name == binding.service_id:
            return requirement
    message = "peer service access is not declared by this consumer"
    raise ExtensionContractError(message)


def _require_identity(binding: ServiceBinding, provider: ServiceProvider) -> None:
    environment = provider.environment
    if environment is None or (
        environment.extension_info.extension_id != binding.owner or provider.manifest.extension_id != binding.owner
        or environment.extension_info.package_version != provider.manifest.package_version
    ):
        message = "peer service metadata does not match its selected worker"
        raise ExtensionContractError(message)


def _package_compatible(consumer: ExtensionManifest, provider: ServiceProvider) -> bool:
    return any(
        dependency.extension_id == provider.manifest.extension_id
        and SpecifierSet(dependency.version_range).contains(provider.manifest.package_version)
        for dependency in consumer.dependencies
    )


def _resolve_service(
    requirement: ServiceRequirement, binding: ServiceBinding, provider: ServiceProvider,
) -> ServiceResolved | ServiceUnavailable:
    for service in provider.manifest.contributions.services:
        if service.name == binding.service_id:
            if not SpecifierSet(requirement.version_range).contains(service.version):
                return ServiceUnavailable(binding=binding, reason="incompatible")
            environment = provider.environment
            if environment is None:
                return ServiceUnavailable(binding=binding, reason="not_enabled")
            return ServiceResolved(
                binding=binding, service_revision=ServiceRevision(
                    package_version=provider.manifest.package_version, service_version=service.version,
                    runtime_revision=environment.runtime_revision,
                ), queries=tuple(
                    definition for definition in provider.manifest.contributions.queries
                    if definition.name in service.queries and binding.scope.kind in definition.scopes
                ),
            )
    return ServiceUnavailable(binding=binding, reason="not_provided")
