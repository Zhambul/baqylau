"""Everything about a harness that does not change while it runs."""

from __future__ import annotations

from dataclasses import dataclass

from domain.ids import HarnessName
from harness.models.catalog import ModelOption, RewindModeOption


@dataclass(frozen=True)
class HarnessInfo:
    """Everything about a harness that does not change while it runs.

    Built once, as a literal, in each plugin's descriptor. That is the whole
    constraint on what may live here: import-time purity forbids file I/O, so a
    fact that has to be READ (the account registry, the session's own slash
    commands) cannot be a field no matter how rarely it changes.
    """

    name: HarnessName
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
    supports_native_automatic_renaming: bool = False
    # Whether compaction yields readable context for people, rather than only
    # an opaque continuation item for the harness itself.
    supports_readable_compaction_context: bool = False
    # A queued Enter is owned by the CLI and drains at its turn boundary. When
    # false, the application keeps the item durable and submits it after the
    # canonical turn-finished fact.
    supports_native_text_queue: bool = False
    # Whether a launch MUST carry a first message. True for a harness that
    # announces its session only once the first TURN begins — its session-start
    # raw event lands with the first prompt, never at startup. Launched with an
    # empty prompt such a CLI comes up, waits at its own input and tells us
    # nothing: the session exists in the terminal and NOWHERE here. So the launch
    # is declined at the door (harness.services.launcher) rather than leaving the
    # dashboard waiting for a session that cannot arrive. A harness whose
    # session-start raw event fires at startup leaves this False and launches bare.
    requires_initial_message: bool = False
    models: tuple[ModelOption, ...] = ()
    rewind_modes: tuple[RewindModeOption, ...] = ()
