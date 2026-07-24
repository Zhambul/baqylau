# dashboard/read/ — the web dashboard's READ-SIDE presentation model.
#
# The consumer read model split out of server.py: the payload builders that turn
# the four session stores (state DB live+parked, audit, tab DB, transcripts) into
# the JSON the browser renders. Pure reads — no control-plane writes, no HTTP.
# Grouped by surface: meta (per-session title/git/ctx/goal), lists (the overview
# payloads), session (one session's detail + its modal cards), mirror (the op
# stream → HTML). server.py (the HTTP layer) is the sole consumer.
