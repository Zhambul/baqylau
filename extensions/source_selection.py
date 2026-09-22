# Copyright (c) 2026 Zhambyl Yermagambet
"""Select ordered source readers from one borrowed registry snapshot."""

from dataclasses import dataclass

from baqylau_extension_api.models.scopes import ExtensionScope

from extensions.models.source_processing import SourcePolicy, SourceScopeKey
from extensions.registry_snapshot import RuntimeSnapshot
from extensions.source_calls import SourceCalls, source_provider
from extensions.source_reader import SourceReader
from extensions.source_resources import SourceCallbacks, SourceServices


@dataclass(frozen=True)
class SourceBatchContext:
    """Bind one manager, runtime, and scope set for the full engine pass."""

    manager_id: str
    snapshot: RuntimeSnapshot
    scopes: tuple[ExtensionScope, ...]


def select_readers(
    context: SourceBatchContext, services: SourceServices, callbacks: SourceCallbacks, policy: SourcePolicy,
) -> dict[str, SourceReader]:
    """Use active dependency order and omit packages with no source capability.

    Returns:
        An owner index valid only during this borrowed batch.

    """
    readers = {}
    for owner in context.snapshot.active_order:
        matches = (package for package in context.snapshot.packages if package.manifest.extension_id == owner)
        package = next(matches)
        provider = source_provider(package)
        if provider is not None:
            readers[owner] = SourceReader(
                SourceCalls(provider, context.snapshot.schemas, services.ledger, policy.call_seconds),
                services.repository, context.manager_id, callbacks, policy,
            )
    return readers


def select_scopes(reader: SourceReader, scopes: tuple[ExtensionScope, ...]) -> tuple[SourceScopeKey, ...]:
    """Call a source provider only for scope kinds declared by its source types.

    Returns:
        Complete host scopes, never feature-created harness or actor identities.

    """
    manifest = reader.calls.provider.manifest
    return tuple(SourceScopeKey(manifest.extension_id, scope) for scope in scopes if any(
        scope.kind in definition.scopes for definition in manifest.contributions.source_types
    ))
