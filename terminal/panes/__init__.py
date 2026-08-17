"""The two panes baqylau paints beside a session, as SERVED things.

`terminal/mirror/` and `terminal/scoreboard.py` build what a pane shows; this
package is how it reaches one. Everything here runs in the DAEMON: `streams`
renders every frame, `commands` runs a gesture, `reaction` opens and closes the
panes on a session's own facts.

The processes at the other end of that are not here and are not importable from
here — a pane and a keybinding are stdlib-only HTTP clients
(`client/terminal_pane.py`, `client/terminal_keys.py`), so nothing they do can
be broken by a change under `terminal/`.

This is the one tier of `terminal/` that reaches for `engine/` and `harness/`:
a pane shows a SESSION, so it needs the projections behind one. The contract
below it stays keyed on window ids and knows none of that.
"""
