"""Claude Code's single public harness-plugin descriptor."""

from contracts.harness import HarnessInfo, HarnessPlugin
from domain.codec import SCHEMA_VERSION
from plugins.claude_code.canonical import ClaudeCanonicalTranslator, ClaudeSessionRecognizer
from plugins.claude_code.canonical_hook import hook
from plugins.claude_code.catalog import ClaudeCodeCatalog
from plugins.claude_code.controller import controller
from plugins.claude_code.launcher import ClaudeCodeLauncher
from plugins.claude_code.lifecycle import lifecycle
from plugins.claude_code.memory_port import memory_reader
from plugins.claude_code.terminal_probe import ClaudeCodeTerminalProbe
from plugins.claude_code.usage_rows import usage_reader

plugin = HarnessPlugin(
    info=HarnessInfo(
        name="claude_code",
        display_name="Claude Code",
        plugin_version="2",
        canonical_version=SCHEMA_VERSION,
        supports_attachments=True,
        default_for_launch=True,
    ),
    sessions=ClaudeSessionRecognizer(),
    events=ClaudeCanonicalTranslator(),
    hook=hook,
    lifecycle=lifecycle,
    controller=controller,
    catalog=ClaudeCodeCatalog(),
    usage=usage_reader,
    memory=memory_reader,
    launcher=ClaudeCodeLauncher(),
    terminal_probe=ClaudeCodeTerminalProbe(),
)
