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
# EVERY shape this layer sends is an api model of its own, under one of those
# models/ trees, with a mapper that builds it from the service object it
# describes. Nothing below api/ knows this layer exists, no route hands back a
# projection dataclass, and no route builds JSON — a response body, an SSE frame
# and an error body are all a model FastAPI (or api/sse.py) serializes.
#
# The daemon builds the application graph exactly once, in api/server.py
# serve(). The OpenAPI documents are served at /openapi.json and /openapi.yaml.
