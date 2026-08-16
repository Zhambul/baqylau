"""Everything about a harness that does not change while it runs."""

from __future__ import annotations

from dataclasses import dataclass

from harness.models.catalog import ModelOption, RewindModeOption


@dataclass(frozen=True)
class HarnessInfo:
    """Everything about a harness that does not change while it runs.

    Built once, as a literal, in each plugin's descriptor. That is the whole
    constraint on what may live here: import-time purity forbids file I/O, so a
    fact that has to be READ (the account registry, the session's own slash
    commands) cannot be a field no matter how rarely it changes.
    """

    name: str
    display_name: str
    plugin_version: str
    canonical_version: int
    # The CLI executable's process name — how the hook process finds the CLI in
    # its own ancestry, and how the liveness check tells the CLI apart from a
    # reused pid.
    cli_process_name: str
    supports_attachments: bool = False
    default_for_launch: bool = False
    supports_accounts: bool = False
    models: tuple[ModelOption, ...] = ()
    rewind_modes: tuple[RewindModeOption, ...] = ()
