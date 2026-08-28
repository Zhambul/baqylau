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

The Playwright setup always builds and stamps the production bundle first. This
applies to `make test-browser`, `npm run test:browser`, and direct Playwright
commands. The tests seed a temporary data directory through the canonical event
pipeline. They then start a second daemon with explicit `--port`, `--data-dir`,
and `--log` values. The suite uses port 8794 by default and runs Chromium and
WebKit. It does not use the normal daemon or its data.

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

This suite is not part of `make test`. Run it on demand. It starts real
applications, launches the real `claude` and `codex` programs in isolated
copies of the configured workspace, and uses real tokens. It detects a harness
change that a simulated test cannot detect.

```sh
make test-drift                                        # every scenario
make test-drift E2E="-k codex"                         # one harness
make test-drift E2E="--e2e-model claude-opus-5"        # every scenario, one model
make test-drift E2E="--e2e-data-dir /tmp/drift"        # keep the databases after
make test-drift E2E_WORKERS=1                           # isolate one scenario for debug
make e2e                                                # every live + browser E2E layer
```

Each worker has one private data directory, one automatic port, one Codex home,
and one Git workspace copy. It cannot use the normal application on port 8377.
It uses the installed harness credentials and hooks. After every scenario the
worker restarts its private application; application shutdown closes every PTY
process group. The next scenario therefore gets a fresh daemon, terminal, and
harness process boundary without paying for a new xdist worker or workspace.
The only run-scoped shared resource is a locked, atomic snapshot of read-only
account usage, which prevents every daemon from launching the same native usage
probes.

The default is 20 live scenarios at a time and four frontend browser tests.
The suite is dominated by subprocess, socket, and remote-model waits rather
than Python CPU work. A measured 20-worker run starts every Codex and Claude
Code session and keeps useful work in flight. xdist uses separate Python
processes, so the GIL does not serialize them.
Dynamic one-test scheduling and a one-test scheduling chunk keep every scenario
independently assignable and prevent fail-fast from leaving a large preassigned
tail. Codex, Claude Code, usage, and automatic-title scenarios all share this
pool. Override `E2E_WORKERS` to measure or stress a different concurrency level.
Use `E2E_WORKERS=1` for real-terminal or installed-daemon cases because those
explicit opt-in suites control one machine-level resource.

`make e2e` is the complete end-to-end gate. It runs the live scenarios, the
live-browser scenarios, and then static Playwright. Playwright rebuilds the
production application before its suite. Every suite uses its measured maximum
reliable parallelism: 20 workers for each live suite and four for static
Playwright. The suite boundaries are serial, so a failed token-spending layer
stops the gate before the next layer starts. Override `E2E_WORKERS` when you
measure another machine. Cases do not share ports, data directories, harness
homes, or workspaces.

`make test-drift` collects only `tests/e2e/test_scenarios.py`, the active live
matrix. Browser drift, real-Kitty, and installed-daemon tests remain separate
explicit commands, so the normal result reports tests that actually ran rather
than a large block of expected skips.

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
diagnostic checkpoint before it starts. At signoff, it closes each active
session, checks that the session and all actors are finished, waits for the raw,
canonical, and reaction pipelines to drain, and restarts the isolated daemon.
The diagnostic report must show a verdict for every raw event, no unknown or
failed interpretation, and no audit error. A second report applies to the
complete test run.

Usage is global, so usage cases do not create a session. Storage migrations are
white-box repository tests and do not run as live Gherkin cases. A future
browser suite can reuse the application process, typed client, and named
references.

## `notify_sink.py`

`tests/notify_sink.py` stands in for the external notify script, so the
notification paths can be exercised without reaching a phone. Nothing to run; it
is wired by the fixtures that need it.
