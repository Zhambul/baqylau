"""The harness concern, whole: the contract, the harnesses, and the channels.

`contract.py` + `models/` are the implementation boundary — one observed
session's evidence in, canonical facts and typed control outcomes out, with no
knowledge of any concrete CLI. `impl/` holds the concrete harnesses, one
directory each, and is imported only by bootstrap (through `installed()`).
`hooks/` is the pushed-evidence channel, both halves. `services/` is the
application-level tier over a resolved plugin: control dispatch, launching,
menus, usage, live input state.
"""
