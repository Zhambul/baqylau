# Copyright (c) 2026 Zhambyl Yermagambet
"""Authorize live source calls and validate both sides of the worker boundary."""

from dataclasses import dataclass
from pathlib import Path

from baqylau_extension_api.contracts.sources import ExtensionSources
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models import environment, source_results, sources
from baqylau_extension_api.models.scopes import ExtensionScope
from baqylau_extension_api.runtime.call_grants import HostCallLedger
from baqylau_extension_api.schemas import SchemaSet
from baqylau_extension_api.sources import batches, plans, registration

from core.input_paths import InputPaths, existing_parent, resolved_inputs
from extensions.models import registry, source_processing, source_reads
from extensions.registry_package import RegistryPackage


@dataclass(frozen=True)
class SourceProvider:
    """Select a non-optional source capability from a validated active registry package."""

    manifest: ExtensionManifest
    environment: environment.ExtensionEnvironment
    settings: registry.RuntimeSettings
    capability: ExtensionSources


def source_provider(package: RegistryPackage) -> SourceProvider | None:
    """Reject inactive metadata without relying on production assertions.

    Returns:
        A borrowed source capability, or no source provider for this package.

    """
    if package.entry.state != "enabled":
        return None
    if package.environment is None or package.plugin is None:
        return None
    capability = package.plugin.capabilities.sources
    if capability is None:
        return None
    return SourceProvider(package.manifest, package.environment, package.settings, capability)


@dataclass(frozen=True)
class SourceCalls:
    """Borrow one active package only while the registry batch remains open."""

    provider: SourceProvider
    schemas: SchemaSet
    ledger: HostCallLedger
    timeout: float

    def describe(self, scope: ExtensionScope) -> source_results.SourcePlan:
        """Give each describe call its own host grant and exact captured settings.

        Returns:
            A complete checked plan before native watches are changed.

        """
        with self.ledger.root(self.provider.environment, scope, self.timeout) as grant:
            context = self._context(scope, grant.call_id)
            registration.validate_source_context(self.provider.manifest, self.schemas, context)
            response = plans.validate_source_plan(context, self.provider.capability.describe(context))
            plans.validate_plan_documents(self.provider.manifest, self.schemas, response)
            _validate_watches(response)
            return response

    def read(
        self, checkpoint: source_reads.SourceCheckpoint, source: sources.SourceDescriptor,
    ) -> source_processing.SourceReply:
        """Read with no SQLite transaction held and no feature-selected authority.

        Returns:
            The exact request and checked response for later atomic acceptance.

        """
        with self.ledger.root(self.provider.environment, checkpoint.key.scope, self.timeout) as grant:
            request = sources.SourceReadRequest(
                context=self._context(checkpoint.key.scope, grant.call_id), source=source,
                after_position=checkpoint.position,
            )
            registration.validate_source_read(self.provider.manifest, self.schemas, request)
            response = batches.validate_source_batch(request, self.provider.capability.read(request))
            batches.validate_batch_documents(self.provider.manifest, self.schemas, response)
            return source_processing.SourceReply(request, response)

    def release(self, scope: ExtensionScope, source_identity: str | None) -> bool:
        """Release only a selected source or scope from the still-active runtime.

        Returns:
            True only after a complete matching release acknowledgment.

        """
        with self.ledger.root(self.provider.environment, scope, self.timeout) as grant:
            request = sources.SourceReleaseRequest(
                binding=self._context(scope, grant.call_id).binding, reason="source selection changed",
                source_identity=source_identity,
            )
            response = self.provider.capability.release(request)
            return plans.validate_source_release(request, response).status == "released"

    def _context(self, scope: ExtensionScope, call_id: str) -> sources.SourceContext:
        return sources.SourceContext(binding=sources.SourceBinding(
            extension_id=self.provider.manifest.extension_id, scope=scope,
            runtime_revision=self.provider.environment.runtime_revision, call_id=call_id,
        ), settings_revision=self.provider.settings.revision, settings=self.provider.settings.for_scope(scope))


def _validate_watches(plan: source_results.SourcePlan) -> None:
    paths = frozenset(Path(path) for source in plan.sources for path in source.watch_paths)
    for root in InputPaths(additional=resolved_inputs(paths)).roots(()):
        selected = existing_parent(root)
        if selected == Path(selected.anchor):
            message = "extension source cannot watch a filesystem root"
            raise ValueError(message)
