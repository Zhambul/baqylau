# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject missing protocol methods at the dynamic factory boundary."""

from baqylau_extension_api.contracts.lifecycle import ExtensionLifecycle
from baqylau_extension_api.contracts.migrations import ExtensionMigrations
from baqylau_extension_api.contracts.observers import ExtensionObserver
from baqylau_extension_api.contracts.operations import ExtensionCommands, ExtensionQueries
from baqylau_extension_api.contracts.plugin import ExtensionCapabilities
from baqylau_extension_api.contracts.presentation import ExtensionTerminalPresenter
from baqylau_extension_api.contracts.processing import ExtensionCanonicalTransformer, ExtensionRawTransformer
from baqylau_extension_api.contracts.projection import ExtensionProjectionTransformer, ExtensionProjector
from baqylau_extension_api.contracts.sources import ExtensionSources, ExtensionTranslator
from baqylau_extension_api.errors import ExtensionContractError


def validate_handlers(capabilities: ExtensionCapabilities) -> None:
    """Check dynamic objects before the worker reports that they are ready."""
    lifecycle: object = capabilities.lifecycle
    terminal: object = capabilities.terminal
    _require_protocol("lifecycle", is_valid=isinstance(lifecycle, ExtensionLifecycle))
    _require_protocol(
        "terminal presenter", is_valid=terminal is None or isinstance(terminal, ExtensionTerminalPresenter),
    )
    _validate_transforms(capabilities)
    _validate_operations(capabilities)
    _validate_sources(capabilities)


def _validate_transforms(capabilities: ExtensionCapabilities) -> None:
    raw: object = capabilities.raw_transformer
    canonical: object = capabilities.canonical_transformer
    projector: object = capabilities.projector
    projection_transformer: object = capabilities.projection_transformer
    _require_protocol("raw transformer", is_valid=raw is None or isinstance(raw, ExtensionRawTransformer))
    _require_protocol(
        "canonical transformer", is_valid=canonical is None or isinstance(canonical, ExtensionCanonicalTransformer),
    )
    _require_protocol("projector", is_valid=projector is None or isinstance(projector, ExtensionProjector))
    _require_protocol("projection transformer", is_valid=(
        projection_transformer is None or isinstance(projection_transformer, ExtensionProjectionTransformer)
    ))


def _validate_operations(capabilities: ExtensionCapabilities) -> None:
    queries: object = capabilities.queries
    commands: object = capabilities.commands
    migrations: object = capabilities.migrations
    observer: object = capabilities.observer
    _require_protocol("queries", is_valid=queries is None or isinstance(queries, ExtensionQueries))
    _require_protocol("commands", is_valid=commands is None or isinstance(commands, ExtensionCommands))
    _require_protocol("migrations", is_valid=migrations is None or isinstance(migrations, ExtensionMigrations))
    _require_protocol("observer", is_valid=observer is None or isinstance(observer, ExtensionObserver))


def _validate_sources(capabilities: ExtensionCapabilities) -> None:
    sources: object = capabilities.sources
    translator: object = capabilities.translator
    _require_protocol("sources", is_valid=sources is None or isinstance(sources, ExtensionSources))
    _require_protocol("translator", is_valid=translator is None or isinstance(translator, ExtensionTranslator))


def _require_protocol(capability: str, *, is_valid: bool) -> None:
    if not is_valid:
        message = f"extension {capability} does not implement its public protocol"
        raise ExtensionContractError(message)
