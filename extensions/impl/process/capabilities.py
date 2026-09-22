# Copyright (c) 2026 Zhambyl Yermagambet
"""Compose the SDK's existing typed proxy classes without copying their RPC methods."""

from baqylau_extension_api.contracts.plugin import ExtensionCapabilities
from baqylau_extension_api.runtime import (
    commands,
    migrations,
    observers,
    presentation,
    projection,
    projection_transforms,
    proxies,
    queries,
)
from baqylau_extension_api.runtime.sources import RemoteSources
from baqylau_extension_api.runtime.translation import RemoteTranslator

from extensions.impl.process.selection import ProxySelection


def process_capabilities(selection: ProxySelection) -> ExtensionCapabilities:
    """Build one public capability group from the verified ready declaration.

    Returns:
        All supported protocol proxies, with absent capabilities left empty.

    """
    return ExtensionCapabilities(
        lifecycle=proxies.RemoteLifecycle(selection.caller),
        sources=selection.select("sources", RemoteSources),
        translator=selection.select("translator", RemoteTranslator),
        raw_transformer=selection.select("raw_transformer", proxies.RemoteRawTransformer),
        canonical_transformer=selection.select("canonical_transformer", proxies.RemoteCanonicalTransformer),
        projector=selection.select("projector", projection.RemoteProjector),
        projection_transformer=selection.select(
            "projection_transformer", projection_transforms.RemoteProjectionTransformer,
        ),
        queries=selection.select("queries", queries.RemoteQueries),
        commands=selection.select("commands", commands.RemoteCommands),
        terminal=selection.select("terminal", presentation.RemoteTerminalPresenter),
        migrations=selection.select("migrations", migrations.RemoteMigrations),
        observer=selection.select("observer", observers.RemoteObserver),
    )
