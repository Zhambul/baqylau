"""The two panes baqylau paints beside a session, as SERVED things.

`terminal/mirror/` and `terminal/scoreboard.py` build what a pane shows; this
package is how it reaches one. The presentation runs in the daemon (`streams`),
the pane process is a thin byte-copying client (`mirror_process`,
`scoreboard_process`), the keybinding is a thinner one still (`client` ships
what only it can observe to `commands`), and `preferences`/`views` hold the
small pieces of state a pane remembers between frames.

This is the one tier of `terminal/` that reaches for `runtime/` and `harness/`:
a pane shows a SESSION, so it needs the projections behind one. The contract
below it stays keyed on window ids and knows none of that.
"""
