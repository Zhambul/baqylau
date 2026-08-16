# terminal/impl/ — the concrete terminals, and the one function that picks one.
#
# A terminal is ONE directory here implementing terminal/contract.py; adding a
# second (ghostty, tmux) needs no edit above this package — it renders its own
# match/anchor syntax in its own module and registers with the detector below.
#
# `resolve()` is the only import boundary crossing out of here: everything else
# in the tree takes a `TerminalPlugin` (or one of its fields) by injection, so
# `terminal.impl` has exactly one importer — bootstrap — plus the two clients
# that can only observe their own window from inside it.
#
# Detection, not a default: a machine with no terminal we can drive gets None,
# and bootstrap wires the null plugin. $BAQYLAU_TERMINAL pins one by name
# ("kitty", or "none" for the inert plugin) and overrides detection.
import os

from terminal.contract import TerminalPlugin


def _kitty():
    """The kitty plugin when kitty's client binary resolves on this machine.

    Only the BINARY is checked, not a live socket: the daemon outlives kitty
    instances, so a socket probe at bootstrap would strand it on the null
    plugin the first time kitty restarted. The channel is resolved per call
    (terminal/impl/kitty/remote.py), and a call with no reachable kitty returns
    its ordinary failure response.
    """
    from terminal.impl.kitty.plugin import kitty_plugin  # noqa: PLC0415 — a detector must not import the terminal it may not select
    from terminal.impl.kitty.remote import find_kitten  # noqa: PLC0415 — a detector must not import the terminal it may not select
    return kitty_plugin() if find_kitten() else None


DETECTORS = {"kitty": _kitty}


def resolve() -> TerminalPlugin | None:
    """The terminal installed here, or None when there is none to drive."""
    pinned = (os.environ.get("BAQYLAU_TERMINAL") or "").strip().lower()
    if pinned == "none":
        from terminal.impl.null import null_plugin  # noqa: PLC0415 — only the pinned terminal is imported
        return null_plugin()
    if pinned:
        if pinned not in DETECTORS:
            raise ValueError(f"unsupported terminal: {pinned}")
        return DETECTORS[pinned]()
    for detect in DETECTORS.values():
        plugin = detect()
        if plugin is not None:
            return plugin
    return None
