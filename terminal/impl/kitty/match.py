# terminal/impl/kitty/match.py — kitty's match-expression micro-language.
#
# `id:42`, `window_id:42`, `var:name=value` are kitty grammar and are rendered
# HERE and nowhere else. Above this directory a caller states intent — a
# `PaneAnchor`, a window id — and each terminal implementation renders its own
# syntax for it. A second terminal has its own equivalent of this file.

from terminal.models.panes import PaneAnchor


def window(window_id):
    """The WINDOW itself."""
    return f"id:{window_id}"


def tab_of(window_id):
    """The TAB CONTAINING the window. kitty's tab-scoped commands (close-tab,
    set-tab-title, set-tab-color) match a tab by a window it holds."""
    return f"window_id:{window_id}"


def tagged(name, value):
    """Windows carrying a user-var tag."""
    return f"var:{name}={value}"


def anchor(pane_anchor: PaneAnchor):
    """A `PaneAnchor` as the match expression `--next-to` takes."""
    if pane_anchor.window_id is not None:
        return window(pane_anchor.window_id)
    # PaneAnchor.__post_init__ rejects an anchor that names neither, so a
    # window-less anchor always carries a tag. The dataclass is what guarantees
    # that; this only restates it where the unpacking depends on it.
    assert pane_anchor.tag is not None
    name, value = pane_anchor.tag
    return tagged(name, value)
