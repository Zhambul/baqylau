# The HTTP layer (`api/`)

The daemon's one HTTP door, rebuilt 2026-08-16 on FastAPI/uvicorn and moved out
of `dashboard/` into its own top-level package. This doc records the layout,
the typed request/response contract, what deliberately did NOT change, and why
the alternatives failed.

## Why it moved, why a framework

The layer lived in `dashboard/http/` as ~1,600 lines of hand-rolled
`http.server` code — mixin-MRO routing (the URL space was readable in no one
place), ~30 repetitions of the isinstance-then-ValueError dance, three
different ways of producing JSON, and a thread pinned per SSE connection. And
the location lied: the dashboard is only ONE client of this door — the harness
hooks (the evidence plane's single write endpoint), both terminal panes, the
pane keybinding and the click handlers all speak to it. The evidence plane's
one write endpoint living inside a presenter package inverted the
architecture's own story.

FastAPI buys: declarative routing, pydantic validation replacing every hand
check (`api/models.py` — the controls envelope is a discriminated union on
`control_name`, so an unknown control or a missing field is a schema-level
rejection), field-named 400s, async SSE generators (an open stream no longer
pins an OS thread), and a real error contract in one place (`api/app.py`).
The cost, named plainly: **fastapi + pydantic + uvicorn are the repo's first
third-party runtime dependencies** (`requirements.txt`).

## Layout

```
core/wire.py        the client↔server wire contract: host, port, X-Baqylau,
                    the four hook identity headers, the body caps — imported by
                    app/daemon_client.py + app/hook_client.py (the app tier no
                    longer imports dashboard at all) and by the server
api/config.py       server policy: ALLOWED_ORIGINS, READONLY, BOOT_ID, caching,
                    gzip threshold (PUBLIC_URL stays dashboard/config.py's —
                    one fact, two consumers: origin admission + deep links)
api/guard.py        the control-plane POST guard as a Depends() — read-only
                    switch, content type, Origin allowlist, X-Baqylau-or-Origin
                    proof, per-route body cap; every rejection is still a
                    `web-reject` state_files row. NO CORS middleware anywhere:
                    never answering a preflight is part of the defense.
api/models.py       every typed request body + the literal response models
api/dependencies.py the one way a route reaches the application graph
api/routes/         one router per plane: evidence (hooks, exact bytes via
                    request.body()) · streams (the three SSE surfaces, async
                    generators, still one direct poll path — no broker) ·
                    control (launch, the 14-control union, panes/views) ·
                    application (prefs/drafts/presence/telemetry) · files
                    (uploads/clipboard/dictation) · read (JSON GETs) · static
                    (whitelist + BOOT_ID stamping, kept hand-written: policy,
                    not plumbing)
api/app.py          build_web_application(graph) — routers, the error contract
                    (400/404/500 all as {"error": …}), selective gzip (SSE is
                    exempt: compressing an event stream buffers the frames it
                    exists to deliver; an EventSource always sends
                    Accept: text/event-stream, which is the routing fact)
api/server.py       serve() — pid-lock, audited stream, the bound socket
                    (backlog 128), build_default_application() exactly ONCE,
                    the interpreter/usage/notifier threads, uvicorn.run
```

## The typed contract

Requests are pydantic models; responses are the frozen projection dataclasses
(`DashboardSessionSnapshot`, `DashboardActivityPage`, …) serialized by their
one owner, `dashboard.activity.to_wire`, plus small literal models (`Saved`,
`Recorded`, `PaneCommandReply`, …). **Deliberately NOT pydantic response
models for the projections**: mirroring ~40 dataclasses would be a second
encoding of every shared shape (the exact drift the single-owner rule
forbids), and pydantic's own serialization differs at the edges the wire
already depends on (`to_wire` renders Decimal as a string; tuples as arrays;
every field always present). The dataclasses ARE the response types; the wire
bytes did not change.

Validation errors are one shape: `400 {"error": "<field>: <message>"}` (an
exception handler over pydantic's error list — the SPA reads `.error` and
always has). `KeyError`/`ValueError`/`TypeError` raised past a route stay the
read/control planes' 400 contract, as before.

## What deliberately did not change

- **The wire.** Every endpoint, status code, JSON shape, SSE frame and audit
  row is as before; `tests/test_canonical_http.py` ran unchanged over the new
  engine except for its server fixture, the deleted stdlib `handle_error`
  test, and one pinned validation message.
- **One graph, one process.** `serve()` in `api/server.py` is still the only
  graph-builder (the architecture test's allowlist moved with it).
- **SSE has no broker.** The streams are the same direct 0.25 s poll over the
  canonical store and the snapshots; async changed who pays for an open
  connection, not the data path.
- **The guard's semantics**, including the sendBeacon-shaped Origin-only
  branch and the `web-reject` audit row.
- **Hook deliveries are exact bytes**, recorded on the request path, never
  parsed by the transport; the reply rides the response.

## Rejected alternatives

- **Pydantic response models for the projections** — rejected above: a second
  owner for every shared shape, and silent wire drift (Decimal, ASCII
  escaping) the golden tests would have had to chase.
- **CORSMiddleware** — answering preflights would WEAKEN the browser-vector
  defense; the guard's whole design is that a cross-origin fetch that tries to
  send `X-Baqylau` dies on an unanswered preflight.
- **GZipMiddleware unconditionally** — it also compresses streaming responses,
  which buffers SSE; hence the Accept-header-routed selective wrapper.
- **Async application services** — the services are synchronous SQLite; routes
  run sync (thread-pool, tokens raised to 100 in the lifespan), only the SSE
  generators are async. Making the interpreter async was never on the table.
- **Keeping the stdlib server and only extracting the package** — fixes the
  location, keeps the plumbing maintenance and the untyped requests forever.

## Operational notes

- `stop` force-closes open connections after `GRACEFUL_SHUTDOWN_SECONDS` (3 s)
  — the SSE streams never close on their own, so a pure graceful shutdown
  would hang.
- uvicorn owns the SIGTERM/SIGINT handlers; `serve()`'s cleanup (threads,
  audit `stream_end`) runs after `run()` returns. Port-busy and lock-denied
  exits are audited exactly as before.
- The test fixture (`tests/test_canonical_http.py _server`) runs the daemon's
  REAL engine configuration (`api.server.build_server`) on an ephemeral port —
  not a TestClient — so the goldens exercise chunked SSE over a socket.
