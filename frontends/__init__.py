# frontends/ — terminal adapters (the "frontend" layer of docs/architecture.md).
#
# A frontend is the ONE place that knows how to talk to a terminal emulator:
# paint a tab colour, enumerate windows/panes, open the mirror split, resize it.
# Everything above this layer (core/, plugins/, the entry scripts) speaks the
# Frontend interface in frontends/base.py, so supporting a new terminal
# (iTerm2, ghostty, …) means adding ONE sibling module here and teaching get()
# to detect it — no other file changes. kitty is the only implementation today.
#
# Selection: $CLAUDE_FRONTEND pins one explicitly ("kitty", or "none" for an
# inert stub); unset defaults to kitty. Detection-by-environment (ITERM_*,
# GHOSTTY_*) slots in here when a second frontend exists.
import os


def get(resolve=False):
    """The active Frontend. `resolve=True` lets the frontend hunt for its
    control channel beyond the environment (kitty: the ppid walk / lone-socket
    fallback claude-split.py needs for keybinding launches — see
    frontends.kitty.resolve_listen_on)."""
    name = (os.environ.get("CLAUDE_FRONTEND") or "kitty").strip().lower()
    if name == "kitty":
        from frontends.kitty import KittyFrontend
        return KittyFrontend(resolve=resolve)
    from frontends.base import Frontend
    return Frontend()                        # "none" / unknown -> inert stub
