# Testing

`make test` is the whole suite and the only gate that matters before a commit;
`make lint` is the other one (types first, then dead code, then ruff — in that
order, because a cheap gate must never mask an important one).

Most of it is ordinary pytest over the Python tree. Two tiers are not, and both
are referenced from the files themselves, so this page exists to say what they
are and how to run them.

## Frontend tests — Vitest and Playwright

The Svelte frontend has two test layers. `make test-frontend` runs strict type
checks, formatting, and Vitest with coverage. The coverage gate applies to the
pure behavior modules and state machines. Playwright covers compiled components
and API boundaries, where statement coverage measures generated component code
instead of useful behavior.

`make test-browser` builds and stamps the production bundle. It seeds a temporary
data directory through the canonical event pipeline. It then starts a second
daemon with explicit `--port`, `--data-dir`, and `--log` values. The suite uses
port 8794 by default and runs Chromium and WebKit. It does not use the normal
daemon or its data.

```sh
make test-frontend
make test-browser
cd dashboard/frontend && npm run test:browser -- --project=chromium
```

The browser suite checks the production manifest, CSP console errors, route and
scope transitions, the session feed, agent and monitor drill-downs, the resume
preview focus boundary, structural accessibility, and stable screenshots. The
screenshots keep the existing design visible as an executable contract.

The old hand-written DOM shim had these replacement owners:

- Account alignment: `application/usage-layout.test.ts` and the list snapshot.
- Questions and control outcomes: control translator tests plus the session E2E.
- Dictation: `dictation-controller.svelte.test.ts`.
- Composer liveness, optimistic prompts, and history: reducer tests plus the
  session and parked-session E2E paths.
- Feed scope, grouping, expansion, and trusted markup: feed, session reducer,
  shell fold, and markup tests plus the session snapshot.
- Route order, first-connect cursor, and reload protocol: route, stream decoder,
  and application shell tests plus the production browser boot.
- New-session, header, monitor, and agent interactions: the production browser
  suite in Chromium and WebKit.

Canonical facts remain in `tests/test_canonical_sessiondata*.py`, where the
writers and HTTP response models are cheaper and clearer to test.

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
