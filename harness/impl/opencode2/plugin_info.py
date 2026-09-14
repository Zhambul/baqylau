# Copyright (c) 2026 Zhambyl Yermagambet
"""Define static OpenCode2 plug-in metadata."""

from domain.events import SCHEMA_VERSION
from harness.impl.opencode2.sources import HARNESS
from harness.models.catalog import RewindModeOption
from harness.models.info import HarnessInfo

CLI_PROCESS_NAME = "opencode2"
DEFAULT_MODEL_ID = "opencode-go/deepseek-v4.1-flash"

HARNESS_INFO = HarnessInfo(
    name=HARNESS,
    display_name="OpenCode2",
    plugin_version="1",
    canonical_version=SCHEMA_VERSION,
    cli_process_name=CLI_PROCESS_NAME,
    supports_attachments=True,
    # The native plugin announces a session only when its first turn starts.
    # A bare launch would leave the session in the terminal and nowhere here.
    requires_initial_message=True,
    supports_native_initial_naming=True,
    # The native compaction end record carries the kept context as text, so a
    # person can read what the session kept rather than only that it happened.
    supports_readable_compaction_context=True,
    rewind_modes=(
        RewindModeOption("both", "Restore conversation and files"),
        RewindModeOption("conversation", "Restore conversation"),
    ),
)
