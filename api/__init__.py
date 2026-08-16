# api/ — the daemon's HTTP layer.
#
# One FastAPI application serving every client of the daemon, split by who
# consumes what:
#   api/common/    — endpoints every client kind shares (hook deliveries — the
#                    evidence plane's one write endpoint — and content)
#   api/dashboard/ — the browser SPA's endpoints
#   api/terminal/  — the terminal-side clients (pane gestures, views, streams)
# Each subpackage carries a models/ tree: one subpackage per section, one file
# per request/response model. Shared plumbing (guard, SSE framing, config,
# the app factory, serve()) lives at this root.
#
# Responses that are projection dataclasses stay dataclasses, serialized by
# the one owner of that encoding (dashboard.activity.to_wire). The daemon
# builds the application graph exactly once, in api/server.py serve(). The
# OpenAPI documents are served at /openapi.json and /openapi.yaml.
