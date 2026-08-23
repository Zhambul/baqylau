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

This suite is not part of `make test`. Run it on demand. It starts the real
application, launches the real `claude` and `codex` programs in a real
workspace, and uses real tokens. It detects a harness change that a simulated
test cannot detect.

```sh
make test-drift                                        # every scenario
make test-drift E2E="-k codex"                         # one harness
make test-drift E2E="--e2e-model claude-opus-5"        # every scenario, one model
make test-drift E2E="--e2e-data-dir /tmp/drift"        # keep the databases after
```

The run has one private data directory and one automatic port. It cannot use the
normal application on port 8377. It uses the installed harness credentials and
hooks. One application process serves all scenarios. This is the same use case
as several normal sessions in one long-running application. It also keeps the
run time and token cost low.

The suite has these layers:

- `api.runtime.DashboardApplication` is the application runtime. The CLI and
  the tests use this same runtime.

- `testkit.process.ApplicationProcess` owns only the child process and its
  process signals. It does not send HTTP requests, read logs, or read a
  database.

- `sdk.BaqylauClient` is the typed client. Its resources launch and control
  sessions, read application state, and read structured diagnostics. Test code
  does not use raw GET and POST operations.

- A session snapshot reads the aggregate first. It then reads all feed pages at
  the aggregate cursor. Thus, all data in the snapshot is from one boundary.

- `testkit.references` stores scenario names such as `"greeting"`,
  `"hello command"`, and `"ticker actor"`. A selector must find exactly one
  product identity before it binds a name. Zero matches cause a wait. More than
  one match causes an immediate failure.

- Step modules separate actions, reference acquisition, and checks. A `Then`
  step checks one fact. Time limits are in `testkit.policy`, not in feature
  text.

The suite does not read application logs or databases. Each scenario records a
diagnostic checkpoint before it starts. At signoff, it closes each session and
checks that the session and all actors are finished. It then waits for the raw,
canonical, and reaction pipelines to drain. The diagnostic report must show a
verdict for every raw event, no unknown or failed interpretation, and no audit
error. A second report applies to the complete test run.

Usage is global, so usage cases do not create a session. Storage migrations are
white-box repository tests and do not run as live Gherkin cases. A future
browser suite can reuse the application process, typed client, and named
references.

## `notify_sink.py`

`tests/notify_sink.py` stands in for the external notify script, so the
notification paths can be exercised without reaching a phone. Nothing to run; it
is wired by the fixtures that need it.
