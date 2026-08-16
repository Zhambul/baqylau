# api/ — the daemon's HTTP layer.
#
# One FastAPI application serving every client of the daemon: the browser
# dashboard, the harness hooks (the evidence plane's one write endpoint), the
# terminal pane processes, the pane keybinding and the click-to-open handlers.
# It lives OUTSIDE dashboard/ because the dashboard is only one of those
# clients — the presenters render, this package serves.
#
# Requests are typed pydantic models (api/models.py); responses are the frozen
# projection dataclasses serialized by the one owner of that encoding
# (dashboard.activity.to_wire), plus small literal response models. The daemon
# builds the application graph exactly once, in api/server.py serve().
