# Baqylau browser extension API

This is a draft package. Wire types are generated from the installed Python SDK.
The view interface passes typed data, a narrow client, theme values, and an abort
signal. It passes no dashboard component or private state store.

An extension builds and owns its ES module, component runtime, and styles. The
dashboard will load the built package through the generic host interface. The
current daemon does not yet serve an extension catalog or its assets.

Use `BAQYLAU_SDK_PYTHON` to select an interpreter with the Python SDK installed,
then run `npm run generate`. Generation is a developer step; the npm artifact
contains generated declarations and does not need Python at runtime.

Run `npm run generate:check` to reject generated-type drift. `npm run lint`,
`npm run check`, `npm run format:check`, and `npm run test:coverage` use the shared
host rules and thresholds.

The `./host` export loads each module and its CSS inside a separate Shadow DOM.
It owns the abort signal, serializes updates, and disposes late mount results.
This is a failure boundary for trusted modules, not a JavaScript security sandbox.
The draft client currently supports peer directory reads only.

Generated declarations also include the Python query and command models. These
are worker contracts, not browser job-acceptance requests. A browser must not
allocate job IDs or claim an active runtime. Typed host job routes and their
client methods remain pending.

From the host checkout, run `npm run test:external -- <artifact-directory>` to
copy the package-owned test fixture to a new temporary directory. Supply the
packed API and dev-tools artifacts. The runner installs them and checks types,
lint, formatting, unit behavior, and Chromium and WebKit lifecycle cases.
This small host test is not the main dashboard integration test.
