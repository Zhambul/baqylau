# api/ — the daemon's HTTP layer.
#
# One FastAPI application serving every client of the daemon, split by WHAT A
# ROUTE DOES rather than by which frontend happened to ask for it first — two
# frontends read the same read model and drive the same gestures, so a package
# named after either was a package that lied:
#   api/hooks/       — the raw event plane's one write endpoint: harness hook
#                      deliveries, recorded verbatim
#   api/telemetry/   — the other two ingests, one module each because they carry
#                      different things from different senders: harness.py (an
#                      OTel export) and browser.py (what the page saw)
#   api/sessiondata/ — the read model: the list, one aggregate, one page of the
#                      feed, and the two streams over them
#   api/controls/    — the write plane: the launch POST and one endpoint per
#                      gesture, the URL being the discriminator
#   api/application/ — what a browser owns rather than what a session is:
#                      preferences and drafts, uploads and dictation, the
#                      catalogs, and the SPA's own files
#   api/terminal/    — the terminal-side surface: pane keybindings, and the live
#                      screen/keys passthrough that is explicitly not read model
#   api/common/      — what more than one of the above needs: health, and the
#                      shared value models and their mapper
# Each subpackage carries a models/ tree, one file per request/response model.
# Shared plumbing (guard, SSE framing, config, the app factory, serve()) lives
# at this root.
#
# EVERY shape this layer sends is an api model of its own, under one of those
# models/ trees, with a mapper that builds it from the service object it
# describes. Nothing below api/ knows this layer exists, no route hands back a
# projection dataclass, and no route builds JSON — a response body, an SSE frame
# and an error body are all a model FastAPI (or api/sse.py) serializes.
#
# The daemon builds the application graph exactly once, in api/server.py
# serve(). The OpenAPI documents are served at /openapi.json and /openapi.yaml.
