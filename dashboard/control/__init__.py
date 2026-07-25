# dashboard/control/ — the terminal-facing CONTROL machinery behind the write
# endpoints: launching and resuming sessions, delivering a composed message to
# the TUI's input box, and the draft/echo bookkeeping around it (launch.py).
# The read side is dashboard/read/; the HTTP layer above both is dashboard/http/.
#
# This file exists to make the directory a real package, like its four siblings.
# It carried no __init__.py until 2026-07-25: imports still worked (PEP 420
# namespace packages), so nothing broke — but a package walk skips a namespace
# directory, and pylint had therefore never analyzed launch.py at all. A 476-line
# module holding every control gesture was invisible to the linter, which is how
# it accumulated a trailing blank line and three re-imports of a module it
# already imports at the top.
