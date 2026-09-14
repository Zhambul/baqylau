# Copyright (c) 2026 Zhambyl Yermagambet
"""Build the OpenCode2 event adapter for integration tests."""

from pathlib import Path

from harness.contract import HarnessPlugin
from harness.impl.opencode2.catalog import OpenCodeCatalog
from harness.impl.opencode2.composer import OpenCodeComposer
from harness.impl.opencode2.controls import controller
from harness.impl.opencode2.gateway import OpenCodeHookGateway
from harness.impl.opencode2.plugin_info import HARNESS_INFO
from harness.impl.opencode2.sources import OpenCodeSources
from harness.impl.opencode2.translator import OpenCodeTranslator


def build_plugin(directory: Path | None = None) -> HarnessPlugin:
    """Build the native event adapter.

    Returns:
        The plugin with optional session registration.

    """
    return HarnessPlugin(
        HARNESS_INFO,
        OpenCodeSources(),
        OpenCodeTranslator(),
        hooks=None if directory is None else OpenCodeHookGateway(directory),
        controller=controller,
        catalog=OpenCodeCatalog(),
        composer=OpenCodeComposer(),
    )


plugin = build_plugin()
