"""The terminal concern, whole: the contract, the terminals, and the surfaces.

`contract.py` + `models/` are the implementation boundary — window ids in,
typed responses out, no session or harness vocabulary anywhere. `impl/` holds
the concrete terminals, one directory each, and is imported only by bootstrap.
`adapter.py` is the session-level service over a resolved plugin. Everything
else here draws the two panes baqylau paints into a terminal.
"""
