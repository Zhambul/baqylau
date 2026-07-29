# dashboard/ext/memory — the MEMORY TAB as a dashboard extension (the first
# one; docs/dashboard.md *Memory tab* / *Web extensions*).
#
# The descriptor: the registry-facing constants + the capability callables,
# every one a thin re-export from read.py (the read model + handlers) so this
# file stays the one-screen answer to "what does this extension plug in".
# The frontend half is dashboard/static/app.11-ext-memory.js (extRegister —
# same NAME); the hook-side producer half is plugins/claude_code/memory.py,
# registered in fileobs.py (PRODUCER below is documentation, never called).
from dashboard.ext.memory.read import badge, get_memory, get_note, scope  # noqa: F401 (descriptor surface)

NAME = "memory"
LABEL = "memory"
TAB_AFTER = "jobs"       # tab strip: mirror · agents · monitors · jobs · MEMORY · errors
BADGE_SCOPED = False     # the tab is team-wide — "session-wide" under agent scope
PRODUCER = "plugins.claude_code.memory"

# GET /api/session/<sid>/memory + /note — the exact wire the hard-coded routes
# served before the extension move (the endpoint names are API vocabulary).
session_get = {"memory": get_memory, "note": get_note}
