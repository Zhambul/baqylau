"""Codex's single public harness-plugin descriptor."""

from contracts.harness import HarnessInfo, HarnessPlugin
from domain.codec import SCHEMA_VERSION
from plugins.codex.canonical import CodexCanonicalTranslator, CodexSessionRecognizer
from plugins.codex.canonical_hook import hook
from plugins.codex.catalog import CodexCatalog
from plugins.codex.controller import controller
from plugins.codex.launcher import CodexLauncher
from plugins.codex.lifecycle import lifecycle
from plugins.codex.usage_rows import usage_reader

plugin = HarnessPlugin(
    info=HarnessInfo(
        name="codex",
        display_name="Codex",
        plugin_version="4",
        canonical_version=SCHEMA_VERSION,
        supports_attachments=True,
    ),
    sessions=CodexSessionRecognizer(),
    events=CodexCanonicalTranslator(),
    hook=hook,
    lifecycle=lifecycle,
    controller=controller,
    catalog=CodexCatalog(),
    usage=usage_reader,
    launcher=CodexLauncher(),
)
