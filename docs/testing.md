# Testing

`make test` is the whole suite and the only gate that matters before a commit;
`make lint` is the other one (types first, then dead code, then ruff — in that
order, because a cheap gate must never mask an important one).

Most of it is ordinary pytest over the Python tree. Two tiers are not, and both
are referenced from the files themselves, so this page exists to say what they
are and how to run them.

## The DOM harness — `tests/jsdom/` driven by `tests/test_dashboard_dom.py`

The dashboard is a hand-written SPA in `dashboard/static/*.js`, and since the
redesign it OWNS its rendering: the daemon serves facts, the browser builds the
markup. That makes the JS load-bearing, and a browser is not available to a test
runner.

So the real modules are loaded into `node` with a hand-written DOM shim
(`tests/jsdom/domshim.js` — no jsdom dependency, no npm, nothing to install).
Each `tests/jsdom/*.js` script builds a sandbox, runs one or more of the real
`dashboard/static/*.js` files inside it, drives them, and prints ONE JSON verdict
object. `tests/test_dashboard_dom.py` runs the script and asserts on that JSON.

```sh
.venv/bin/python -m pytest tests/test_dashboard_dom.py -q     # all of them
node tests/jsdom/taskorder.js \
  dashboard/static/app.00a-markup.js \
  dashboard/static/app.00b-entries.js \
  dashboard/static/app.05-session.js                 # one, by hand, for its raw JSON
```

**Every case here SKIPS when `node` is absent.** That is deliberate: `node` is
not a build requirement of this project and never becomes one. It is present on
the machines this is developed on, and the cases that need it are the ones no
other tier can express.

What belongs here is behaviour a grep cannot check and a Python test cannot
reach — where an entry LANDS in a newest-first feed when its anchor arrived on an
earlier tick, whether a density pass hides the right rail, whether a dialog
submits what it displayed. What does not belong here is anything about the facts
themselves: those are `tests/test_canonical_sessiondata*.py`, over the writers
and the routes, where they are cheaper and clearer.

The two pure modules — `app.00a-markup.js` (markdown and ANSI to HTML) and
`app.00b-entries.js` (one entry to the markup that draws it) — export under
`module.exports` when `module` exists, so a script can require them directly
instead of through a sandbox. They are pure by design for exactly that reason.

## The live-harness suite — `tests/e2e/`, `make test-drift`

Excluded from `make test` and run on demand, because it starts the REAL daemon,
launches the REAL `claude` / `codex` CLI against a real workspace, and spends
real tokens. It exists for the one failure nothing simulated can catch: a harness
release changing its evidence under an integration that keeps reporting success.

```sh
make test-drift                                        # every scenario
make test-drift E2E="-k codex"                         # one harness
make test-drift E2E="--e2e-model claude-opus-5"        # every scenario, one model
make test-drift E2E="--e2e-data-dir /tmp/drift"        # keep the databases after
```

It isolates OUR state and nothing else: a private data directory (both
databases) and a private port, so a run never touches the daemon on 8377. The
harness's own configuration — credentials, installed hooks — is deliberately the
real one.

Everything goes through the product's own surface. The daemon runs with
`BAQYLAU_TERMINAL=pty`, so it owns the harness's terminal; a launch is
`POST /api/sessions`, the same request the new-session form makes; a prompt is
the `send-text` control; and the two raw-key interactions (the first-run trust
gate, the backgrounding chord) go through the window-addressed terminal
passthrough. Assertions read `/sessionData` — the aggregate and the entry
feed — plus a direct read-only look at the two databases for the machinery
verdicts, which have no route by design.

## `notify_sink.py`

`tests/notify_sink.py` stands in for the external notify script, so the
notification paths can be exercised without reaching a phone. Nothing to run; it
is wired by the fixtures that need it.
