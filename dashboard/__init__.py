# dashboard/ — the web dashboard, a CONSUMER package (docs/dashboard.md).
#
# The browser-facing sibling of the pane renderers: it reads session data
# through core/sessionapi.py and plugins.activity() — the same single door the
# mirror and scorebar use — and serves it over localhost HTTP. Dependency tier:
# dashboard imports core + the plugins registry; NOTHING imports dashboard
# except its bin/ entry (bin/claude-dashboard.py) and the tests. It writes no
# session state (all reads are the API's mode=ro probes); its only writes are
# its own singleton pid-lock (core/locks.py on paths.DASH_DB) and audit rows.
#
#   opshtml.py  — the web PRESENTER of the paint-op vocabulary (core/ops.py):
#                 ops -> HTML, with html-escaping as the neutralize() analog
#   server.py   — the HTTP server: JSON API + SSE + the notification watcher
#   static/     — the single-page app (vanilla JS/CSS, no build step)
