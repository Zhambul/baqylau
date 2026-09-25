# Writing a Baqylau extension

This guide is for authors of external extension packages. An extension package
is a directory with a manifest, an optional Python backend, optional web
assets, and its own tests. The host never imports the package's code into the
daemon: a backend runs in a private worker process, and a web view runs in the
browser from its checked assets. `examples/hello-extension/` is a complete small
package that follows this guide; `tests/extension_testkit/test_example_package.py`
builds it and runs its own case.

The API version is `0.1.0a1`. It is a draft: pin it exactly (`==0.1.0a1`).

## Packages to install

| Package | Use |
| --- | --- |
| `baqylau-extension-api` | Python protocols, wire models, manifest models, validators, and the worker runtime. |
| `@baqylau/extension-api` | TypeScript types and the view lifecycle for web views. |
| `baqylau-extension-testkit` | A private host from the installed `baqylau-dashboard`, typed reads, signoff, browser and terminal helpers, the coverage runner, and the `baqylau_host` pytest fixture. Install `baqylau-extension-testkit[browser]` for browser cases. |
| `baqylau-dev` and `@baqylau/dev-tools` | The shared lint, type, design, dead-code, and test policy. |

## Start a new package

`python -m baqylau_dev new --root <directory> --id <extension-id>` writes a new
package into an empty directory. Add `--web` for a workspace page and
`--terminal` for a Kitty status view. The package has a backend with a
greeting query, a unit test, one kit E2E case that covers each part, the
shared quality profile, and a `Makefile` with three targets:

| Target | Runs |
| --- | --- |
| `make lint` | The shared Python gates. With `--web`, also `make lint-web`: the type check of the web module, ESLint with the shared rules, the tool pin check, and Prettier. |
| `make test` | The unit tests. |
| `make e2e` | The kit runner. Set `BAQYLAU_HOST_EXECUTABLE` and `BAQYLAU_SDK_WHEEL` first. |

The web part is plain JavaScript with JSDoc types from `@baqylau/extension-api`,
so no build step changes the module after the manifest records its digest. If
you change `web/view.js`, write its new digest into `extension.json`. The npm
packages are not published yet: before `npm install`, point the two
`@baqylau` development dependencies at the release tarballs with
`npm pkg set devDependencies.@baqylau/dev-tools=file:<tarball>` and the same
command for `@baqylau/extension-api`. Do not commit these local paths.

## Package layout

```text
extension.json            the manifest
hello_backend.py          the backend module named in the manifest (flat or under src/)
requirements.lock         exact, hash-pinned runtime requirements of the backend
wheels/                   every wheel that the lock names, for offline installs
web/hello.js              web assets, each declared with its SHA-256 digest
tests/e2e/test_hello.py   the package's own E2E cases
```

## The manifest

`extension.json` is one `baqylau_extension_api.manifest.package.ExtensionManifest`
document. Unknown fields are refused. Check it with
`baqylau_extension_api.manifest.validation.validate_manifest`. The main fields:

- `extension_id`: a dotted identifier that you own, for example `example.hello`. Every view, query, command, event type, entry type, and collection name starts with it.
- `api_requires`: the SDK version range, for example `==0.1.0a1`; `quality_policy`: the shared policy version.
- `backend`: `module`, `factory` (default `build_extension`), and `environment` (`requirements` and `wheelhouse`). The host installs the wheels offline into a private environment. Each requirement is pinned with `==` and a full SHA-256 hash; URLs, local paths, editable installs, and nested requirements are refused.
- `capabilities`: exactly the capabilities that the backend returns. A backend always has `lifecycle`.
- `schemas`: JSON Schema documents that you own. A schema reference names the owner, name, version, and the SHA-256 digest of the exact schema text.
- `contributions`: event types, entry types, source types, record collections, processing selections, queries, commands, public services, consumed services, web views, terminal views, and processes.
- `dependencies` and `load_order`: other packages that this package needs (required or optional) and processing order.
- `settings`: default settings, the scopes that can override them, and secret names.
- `migration_paths`: exact settings and record schema conversions.
- `assets`: each web file with its digest and media type.
- `e2e`: the package's own test cases (see "Tests").

## The backend

The backend module has a factory that the worker calls with
`ExtensionHostServices`: the package environment and only the host services
that the manifest declares (peer services, credentials, processes, inference,
records, observations, and audit). The factory returns an `ExtensionPlugin`
whose `capabilities` has one object for each declared capability. The
lifecycle's `activate` returns `ActivationReady` for the requested runtime
revision; `deactivate` stops everything that the package started.

| Capability | Protocol | Runs |
| --- | --- | --- |
| `sources`, `translator` | `ExtensionSources`, `ExtensionTranslator` | reads of declared source files, and pure translation into facts |
| `raw_transformer`, `canonical_transformer` | `ExtensionRawTransformer`, `ExtensionCanonicalTransformer` | pure changes of input before it is accepted |
| `projector`, `projection_transformer` | `ExtensionProjector`, `ExtensionProjectionTransformer` | pure records and feed entries from accepted facts |
| `observer` | `ExtensionObserver` | durable jobs after facts are committed |
| `queries`, `commands` | `ExtensionQueries`, `ExtensionCommands` | declared reads and writes |
| `terminal` | `ExtensionTerminalPresenter` | Kitty views from blocks |
| `migrations` | `ExtensionMigrations` | settings and record conversions |

Rules that the host enforces:

- Pure calls (translation, raw and canonical transforms, projection, and projection transforms) run on the engine thread, so they delay all input. Their deadline is `BAQYLAU_EXTENSION_TRANSFORM_SECONDS` (default 5 seconds). Other calls have `BAQYLAU_EXTENSION_CALL_SECONDS` (default 30 seconds).
- A canonical transformer receives the scope's earlier facts only when its processing selection sets `"prior_state": true`. They cost up to 1,000 facts or 1 MiB for each input, so ask for them only when the transform reads them.
- After 5 consecutive failures (`BAQYLAU_EXTENSION_FAILURE_LIMIT`), the host disables the extension through a recorded failure operation. A failed or rejected call keeps the input unchanged.
- A result is checked completely. One invalid part discards the whole result, and the diagnostic names the failure.
- A write command needs `expected_state_revision`; your command compares it with your real state and refuses stale work. Declare `reconciliation` so that a lost reply can be proved without a repeated write. Read-only hosts refuse write commands.
- Worker log output is bounded (1 MiB for each runtime).

## Cooperation with other packages

A package publishes a service (`services`: a name, a version, and public
queries) and another package consumes it (`consumes`, and a `dependencies`
entry). The consumer uses `host.service_access.resolve_service(...)` and
`query_service(...)`. An absent or disabled peer is an explicit
`unavailable` result, for example `not_enabled`; an optional peer's removal
ends only that integration. Pure processing cannot call peers. A repository view that reads session-scoped peer data sets `uses_sessions` and calls `host.sessions.repository_sessions(...)`. The call gives the sessions whose working directory is in the repository, newest first, up to 200. Dependency and
load-order cycles are refused with the rule that failed.

## Web views

A web view names a slot (`workspace_page`, `session_tab`, `toolbar`, `status`,
`feed`, `settings`, `mirror`, `scoreboard`), its scopes, and its module. A feed
view can `add` rows or `replace` one core entry kind (`target`). The module
exports `mount(target, context)` and returns `{ update(context), dispose() }`.
The context has the extension and view IDs, the scope, the runtime and settings
revisions, the settings, the theme, an abort signal, and `api`. The `api` acts
only on the view's own extension and scope: `listExtensions`, `readSettings`,
`saveSettings`, `query(queryId, argumentsJson, page?)` (the host checks the
arguments and the result against the query's schemas), and
`watchChanges(listener)`, which calls the listener after the extension's
records change in the scope and returns a stop function. An open watch holds the
scope, so the scope's sources stay active while the view is open.
`runCommand(commandId, requestKey, argumentsJson, expectedStateRevision)`
submits one declared command and gives its job; the same request key gives the
same job, so a retry after a lost reply does not run the write again.
`readJob(jobId)` reads the job: its state, and the result document or the
diagnostic when it ends. The host runs command jobs one at a time.

A repository scope names one worktree. Use the SDK's rule
(`baqylau_extension_api.repositories`) to make one: run Git with
`REPOSITORY_ARGUMENTS` and read its output with `repository_from_git`, so the
host and every package give a repository the same ID. A `workspace_page` view
that declares the `repository` scope opens at `#/repo/<directory>/x/<extension>/<view>`;
the session page links to it with the session's directory, and the host
resolves the directory to its repository. The Kitty pane selector also offers
the repository of the window's session. The view renders in a
shadow root, and its styles stay inside it. The host serves each asset only by
its declared digest.

## Kitty views

A terminal view names its scopes and a pane (`mirror` and `scoreboard` add
sections to those core panes). The presenter returns a `TerminalView` of blocks:
`TextBlock`, `SectionBlock`, `TableBlock`, `ListBlock`, `FileTreeBlock`,
`DiffBlock`, and `StatusBlock`. Items can name an action; an action names a
declared command with its arguments and expected state revision. The host
checks the view, draws it in the pane, and runs actions through the normal
command checks. The pane repaints after record changes in its scope.

A presenter is pure, so it cannot read live data. A view that must show live
data names one of the package's queries in `query`. The query must accept every
scope of the view. For each presentation, the host runs the query with a
`TerminalViewInput` (the pane's focus, or none) as its arguments, and the
presenter gets the ready result in `request.document`. A failed query shows the
view as failed.

## Settings and migrations

Settings are one document for each scope, validated by your schema. The host
keeps secrets in the macOS Keychain, never in the database. When a schema
changes, declare an exact migration path; the host converts settings and records
before activation and keeps the old version if the conversion fails.

## Tests

Declare each case in `e2e` with its surfaces (`worker`, `api`, `web`, `kitty`)
and the harnesses that it tests. A case that does not test every harness that
the host discovers needs a `harness_limit` with a reason. A web view needs a
`web` case, a terminal view a `kitty` case, a backend a `worker` case, and each
dependency a case that names it in `peers`.

Run the cases through the runner:

```sh
BAQYLAU_HOST_EXECUTABLE=/path/to/bin/baqylau-dashboard \
  python -m baqylau_extension_testkit.runner path/to/package [--live]
```

The runner checks coverage first and stops with exit code 3 if coverage is
incomplete. A case marked `@pytest.mark.baqylau_live` needs a real terminal or
harness; it runs only with `--live` and is counted apart. In a case, the
`baqylau_host` fixture gives a private host: copy your package into
`baqylau_host.roots.packages`, then call `baqylau_host.start()`. The kit refuses
port 8377 and your normal data directory, and the host gets a private home and
a keyring that refuses secrets. `signoff(client)` waits until all raw,
canonical, projection, and job work is done, then fails on any raw event without
a verdict or any recorded host error.

A session made only of hook input has no terminal window, so the host records a
source error for it. Test session behavior with a live harness case.

## Build the environment

Create the lock and the wheelhouse from your package's runtime requirements:

```sh
uv pip compile requirements.in --generate-hashes -o requirements.lock
pip download --only-binary=:all: --no-deps -r requirements.lock -d wheels
```

Then check the package with the shared gates (`baqylau-dev`) and its cases
(the runner).

In a test, `baqylau_extension_testkit.wheelhouse.build_environment(package)`
writes both from the current Python environment. It packs the SDK's
dependencies and the runtime dependencies in the package's `pyproject.toml`
(`[project].dependencies`), with their dependencies. Install the package's
dependencies in the test environment first. The shared gates refuse an import
of a distribution that `pyproject.toml` does not declare.
