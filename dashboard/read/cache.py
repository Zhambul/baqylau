# dashboard/read/cache.py — the read model's shared memo vocabulary.
#
# One home for the caching primitives every read-side payload builder leans on,
# so a memo cap / fingerprint isn't re-encoded per module. Every path-keyed memo
# is a process-lifetime cache in a days-long singleton — bounded with
# API.BoundedLRU so the KEY set (transcript/state-DB paths, cwds — one per
# session ever seen) can't grow without limit. The cap is far above the live
# working set (SESSIONS_LIMIT sessions + their agents), so an active session
# never thrashes; only paths that scrolled out of discovery age out, and their
# re-derivable values just re-read once if seen again.
from core import sessionapi as API

MEMO_CAP = 8192

# The (path, sig) memo + fingerprint live in core/sessionapi.py (db_sig/
# db_cached — the accounts read model needed them too); these aliases keep the
# call sites reading as before.
_db_sig = API.db_sig
_db_cached = API.db_cached
