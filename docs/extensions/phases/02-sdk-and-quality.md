# P02 — SDK packages and shared checks

Status: in_progress

Owner: Codex

Depends on: P01

Context: The host has strict lint, type, design, dead-code, and architecture checks. External packages need these same checks without importing or copying private host configuration.

Task: Package the public APIs and extract one shared quality policy. Make both the host and a clean external extension consume it.

Outcomes: Installable Python and npm packages, a small extension template, common gate commands, and policy parity tests.

Verification: The build, lint, and unit portions of C26 and the quality cases in C27 pass. P08 completes the installed E2E portion of C26. The host retains its existing effective rule strength.

Evidence: Installable draft Python and npm APIs exist. The host uses the shared Python policy and gate runner. The host and web SDK also consume the first shared frontend rules. See the work records below. Complete architecture, dynamic-entry, template, and release gates remain open.

## Read first

Read [quality](../quality.md), [protocols](../protocols.md), current `Makefile`, `requirements-dev.txt`, `ruff.toml`, `mypy.ini`, `setup.cfg`, frontend configuration files, and `.github/workflows/test.yml`.

### P02-T01 — Build installable API artifacts

Status: done

Owner: Codex

Depends on: P01-T05

Context: The current main application has no root package build system. External extensions must import public APIs through normal installed packages.

Task: Add standard package metadata for `baqylau-extension-api` and `@baqylau/extension-api`. Include Python typing markers, runtime codecs, schemas, TypeScript declarations, and public exports. Keep internal host modules out of the artifacts. Add editable development installation for host contributors.

Outcomes: A Python wheel and npm package that contain the public API. Package versions and schema digests are recorded. No need to package the entire host to import extension types.

Code areas: Proposed `packages/extension-api/`, `packages/extension-api-web/`, host dependency setup and CI artifact jobs.

Verification: Install artifacts in clean environments. Import every public symbol and type-check a small extension. List artifact contents and reject private host packages. Check package metadata and dependency consistency.

Evidence: The Python SDK wheel and its isolated worker checks are recorded in P01. The web SDK artifact contains generated wire declarations, the view contract, and the generic view loader. A clean external package imports only the installed SDK and tools. Artifact contents, hashes, and tests are recorded below. The API is still a draft; complete capability exports and release metadata remain open.

Status result (2026-09-25): Capability exports are complete: all 12 capability protocols are public modules under `baqylau_extension_api.contracts`, and `tests/extension_api/test_public_roots.py` checks the public roots that external packages use. Release metadata: the three Python packages declare authors and classifiers (alpha, Python 3.12, typed), and the two npm packages declare their author. `tests/extension_api/test_release_artifacts.py` builds the SDK, test kit, and policy wheels and requires that each one contains only its own package, its `py.typed` marker, and its metadata, with no host module. License, decided when the user said to resolve it (2026-09-25): proprietary, all rights reserved (`LICENSE`). The three Python packages declare `license = "LicenseRef-Proprietary"` (PEP 639), and the two npm packages declare `"license": "UNLICENSED"`. This states the terms that were already true without a license, and it gives no new permission. An open-source license can replace it later.

### P02-T02 — Extract the Python quality policy

Status: done

Owner: Codex

Depends on: P02-T01

Context: Python rules are spread across standalone configuration files and Makefile arguments. Some host test modules have legacy mypy exceptions that new extensions must not inherit.

Task: Create the shared Python policy package with the current tool pins and common rules. Preserve Ruff, strict mypy, WPS, and Vulture behavior. Use base resources and thin generated configuration for tool-specific paths. Separate host legacy exceptions from common rules. Make the host consume this policy.

Outcomes: One source for Python versions, rules, and gate options. Host and extension profiles differ only where their roots, entry points, or explicit host debt require it.

Code areas: Proposed `packages/dev-tools/`; current `ruff.toml`, `mypy.ini`, `setup.cfg`, `requirements-dev.txt`, `Makefile`, `vulture_allowlist.py`.

Verification: Compare old and new effective host settings. Run type, dead-code, WPS, and Ruff gates. Place one violation for each gate in a fixture extension and require failure. Test config path resolution outside the main checkout.

Evidence: `packages/dev-tools/` builds `baqylau-dev` with the common rule resources and exact tool pins. The host consumes it through `requirements-dev.txt`, `baqylau-dev.toml`, generated editor configs, and Make targets. Host-only mypy debt and runtime annotation readers stay in `quality/`. Forty-eight tests cover parity, path resolution, strict external tests, tool drift, and deliberate failures in every Python gate. The installed wheel passes those tests with child gates outside the checkout. See the shared Python policy work record below. Full host lint still reports the same existing missing callers and unrelated WPS findings; no rule was weakened to hide them. This closes policy extraction, not the dynamic-entry adapter in P02-T04 or the release gates in P02-T06.

### P02-T03 — Extract the frontend quality policy

Status: done

Owner: Codex

Depends on: P02-T01

Context: Frontend rules, tool pins, and coverage thresholds are declared in the dashboard package. External Svelte views need the same checks and their own source roots.

Task: Create `@baqylau/dev-tools` with shared ESLint, TypeScript, Prettier, Knip, Vitest, and Playwright configuration. Keep current pins and thresholds. Expose configuration factories that accept a package root and entry declarations. Make the dashboard consume those exports.

Outcomes: One frontend policy source used by dashboard and extensions. Common scripts keep the current command names. Coverage includes extension behavior modules instead of copying the dashboard file list.

Code areas: Proposed `packages/dev-tools-web/`; current `dashboard/frontend/package.json`, lockfile, ESLint, TypeScript, Prettier, Knip, Vite, and Playwright configs.

Verification: Compare effective rule and compiler settings. Run the existing frontend gates. Require failure for explicit `any`, invalid Svelte props, unused exports, bad formatting, and coverage below the shared thresholds. Validate a built external package under C26.

Evidence: `packages/dev-tools-web/` owns the shared ESLint, TypeScript, Prettier, and coverage rules. The dashboard and web SDK use them. Effective dashboard ESLint rules, globals, parser options, and both TypeScript compiler configurations match the pre-extraction values. Host and SDK gates pass. The external Svelte package uses the installed shared exports. Knip and test profile factories, complete tool pin policy, deliberate violation tests, and the parity runner remain open.

Status result (2026-09-25): The remaining items are done. Factories: `@baqylau/dev-tools/knip` (`knipConfig`) and `@baqylau/dev-tools/vitest` (`testProfile`, with the shared provider, reporters, and thresholds); the dashboard and the web SDK use them (`knip.config.ts`, `vite.config.ts`), and their own lists stay local. Tool pins: `@baqylau/dev-tools/pins` names the exact version of each quality tool, and `baqylau-dev-tools-pins` in each `lint` script (dashboard, web SDK, and the external fixture package) refuses another version. npm peer dependencies were tried first; npm 10.9 fails with an internal resolver error (`#loadPeerSet`) for a linked or local install of any package with peers, so they are not used. Violation tests and parity: `packages/dev-tools-web/test/` is a private consumer package; `make test-dev-tools-web` (part of `make test-frontend`) runs 13 checks: a clean sample passes ESLint, svelte-check, Knip, Prettier, and coverage; an explicit `any`, a Svelte prop of the wrong type, an unused export, bad formatting, and coverage below the thresholds each fail; the dashboard keeps every shared ESLint rule (it adds only its import boundary) and every shared TypeScript option; and the pin check names another version. C26: the external view package, installed from the built tarballs, passes format, types, lint with the pin check, unit, and browser checks (`npm run test:external`). That run also found that the fixture's two client stubs had only `listExtensions`; they now use one offline client with every method.

### P02-T04 — Reuse architecture and dynamic-entry checks

Status: done

Owner: Codex

Depends on: P02-T02, P02-T03

Context: Current architecture checks infer the host root from their own file paths. Vulture and Knip also need to know about entry points invoked by the extension host.

Task: Extract reusable rule functions with explicit roots and package inventories. Add extension profiles for public SDK imports, protocol declarations, schema boundaries, data ownership, and manifest entries. Feed only verified manifest roots to dead-code checks. Retain stale-exemption checks.

Outcomes: Shared architecture checks for host and external packages. Dynamic entry points are visible without blanket lint ignores. Private host and peer-extension imports fail.

Code areas: Current `tests/architecture_*.py` and `tests/test_architecture_*.py`; proposed shared architecture modules and manifest-root adapter.

Verification: C27 rejects wrong protocol signatures, undeclared implementations, private imports, direct storage access, undeclared entries, and missing E2E declarations. A valid dynamic factory does not produce a false dead-code result. A stale exemption fails.

Evidence: The first Python subset is implemented. Host and external checks share protocol parameter rules. The external runner validates backend manifests, source roots, imports, factory types, and SDK protocol implementations before it filters exact Vulture locations. The fixture owns a worker E2E case. See the 2026-09-15 work record below. Complete local protocol rules, schema and I/O boundaries, peer public contracts, test coverage declarations, and frontend dynamic entries remain open.

Status result (2026-09-25): The remaining items, one by one:

- Source and command I/O placement (new): `baqylau_dev/io_checks.py` refuses a process module (`subprocess`, `multiprocessing`, `pty`) anywhere in extension source, because programs run through the host process service with declared bounds, and refuses a network module in a module that declares a pure capability (transforms, translation, projection, presentation, migration), which runs on the engine thread. `tests/dev_tools/test_io_placement.py`.
- Nested configuration (new): the extension profile refused rule files only at the package root; a tool reads the nearest configuration, so a nested `ruff.toml` or `setup.cfg`, or a nested `pyproject.toml` with tool rules, changed the rules of its folder. `parity.py` now searches the package tree and skips installed, built, and hidden folders. `tests/dev_tools/test_nested_rules.py`.
- Default methods (new): a protocol method with a body is a default method, which an implementation inherits, so it is not a required member (`protocol_catalog.required_methods`); the SDK's protocols have none today. `tests/dev_tools/test_protocol_defaults.py`.
- Frontend manifest entries and Knip (new): `knipConfig({ root, manifest })` adds the web view modules that `extension.json` declares as source; a module in `dist/` is a build output. Two checks in `make test-dev-tools-web`.
- Test surface and harness coverage: the kit runner refuses a package whose E2E cases do not cover its declared surfaces and harnesses (P08-T02, `baqylau_extension_testkit.coverage`).
- Peer public contracts: the manifest refuses a peer schema reference or a consumed service without a declared dependency (SDK manifest validation).
- Typed documents and schema boundaries: every document is checked against its declared schema before it crosses a boundary (SDK validators), and pydantic document fields are dead-code entries (`model_fields.py`).
- Package-defined protocols: a package's own `Protocol` is structural typing inside the package, and the dead-code gate follows its methods by name; requiring explicit bases for it, as for the SDK protocols, would refuse valid local typing, so it is not added.

### P02-T05 — Provide the extension template and gate runner

Status: done

Owner: Codex

Depends on: P02-T04

Context: The user wants the same tools for every extension. A template must keep that requirement after its initial creation.

Task: Provide a minimal package template with backend, optional web and terminal parts, schemas, and package-owned tests. Add thin Makefile and npm command wrappers around installed tooling. Declare the quality policy release and lock its tools. Validate effective configuration against the shared policy.

Outcomes: Proposed `make lint` and unit test commands work in an external package. Browser and E2E wrappers dispatch declared checks and report unavailable test support as incomplete; P08 supplies the complete installed test kit. No committed developer-specific paths, copied rule sets, or runtime tool installation.

Code areas: Proposed SDK template assets and shared gate runners; extension-local package metadata and CI template.

Verification: Build a fresh package in a temporary repository. Remove host import paths and aliases. Run available quality and unit gates. Test browser and E2E dispatch with controlled fixture commands; do not report those as integration results. Change a pinned tool or lower a rule and require the parity gate to fail. Backend-only and web-only profiles still check all source that is present.

Evidence: The installed Python runner now provides parity, type, dead-code, WPS, Ruff, and unit commands. An external Python quality fixture proves success and deliberate failure through those commands. It is not the full extension template. Verified dynamic entries, backend and view templates, frontend dispatch, E2E dispatch, and full source-inventory checks remain open.

Evidence added (2026-09-25): `python -m baqylau_dev new` writes a package from template files in the `baqylau-dev` wheel (`templates.py`, `template_manifest.py`, `template_declarations.py`, `template_parts.py`, `template/`). The manifest is built and checked with the SDK's own models. The package has a backend with a greeting query, optional web and terminal views, a unit test, one kit E2E case whose surfaces follow the chosen parts, and thin `make lint`, `make test`, and `make e2e` wrappers. The web profile adds npm wrappers (`make lint-web`): a JSDoc type check against `@baqylau/extension-api`, ESLint with the shared rules, the tool pin check, and Prettier. There is no bundler, so the committed module is the digested module. Verification:
- `tests/dev_tools/test_template.py`: the backend-only package and the package with both views pass the shared Python gates and their unit tests. There is no host path. A package that adds a Ruff ignore, or names another policy release, fails the parity gate. The command refuses a directory that has files.
- `tests/extension_testkit/test_template_package.py`: a package with both views passes its own case through the installed kit runner, with an isolated interpreter and only `BAQYLAU_HOST_EXECUTABLE` and `BAQYLAU_SDK_WHEEL` from the host. The coverage check finds the worker, API, web, and terminal surfaces.
- `make release-artifacts && make test-template-web`: the web profile installs the two npm release tarballs offline and passes `lint-web`. A changed ESLint pin fails the pin check, and a wrong context field fails the type check (manual runs in a generated package). Each consumer target runs in a new temporary directory outside the checkout.
The browser and terminal checks of the E2E case run through the kit. `-m kitty` real-window runs still need approval, so the terminal surface here uses the kit's terminal view read, not a real Kitty window. The extension CI template is in P02-T06.

### P02-T06 — Prove host parity and package release metadata

Status: in_progress

Owner: Codex

Depends on: P02-T05

Context: A shared policy is useful only if the host also uses it and extension CI cannot silently select weaker settings.

Task: Add host and extension CI steps for dependency checks, policy digests, tool version reports, and package artifacts. Keep current host gate ordering and platform coverage. Record Python and JS Playwright pins separately. Define policy release updates for maintained extensions.

Outcomes: CI reports the same shared policy for host and external packages. Existing host quality checks remain active. The artifacts can be consumed without publishing them publicly during development.

Code areas: Current `.github/workflows/test.yml`, dependency setup; proposed shared package workflows and extension CI template.

Verification: Run `make lint` and the current host `make test` path on a supported environment. Confirm that every required gate can fail the job. Compare dependency and policy reports for host and fixture extension. Record live test requirements that need a separate runner.

Evidence: The root frontend gates now include the web SDK checks, generated-type drift check, and shared coverage thresholds. CI installs and builds the shared tooling package before the dashboard and caches all three lockfiles. Local gates pass. No remote CI result, policy digest report, external artifact CI job, or complete P02 release proof is recorded yet.

Evidence added: The Python runner reports the policy version, digest, and all eight installed tool pins. CI now invokes `make policy-check` after dependency installation. Local host and clean-wheel reports have the same digest. No remote CI result or full extension release report is claimed.

Evidence added (2026-09-25): The external artifact CI job is in `.github/workflows/test.yml` (`release`). It runs after the `test` job on Linux, so the host gates and their order in `test` do not change. It builds `dist/release`, runs `make test-release-consumers`, and keeps `dist/release` as an artifact of the run for 14 days. This is not a publication. `make test-release-consumers` does these steps:
- It installs only the three wheels in a clean environment.
- It writes a new package there, and requires that its `baqylau_dev report` is the same as the host's `policy-report.txt` (policy SHA-256 and all eight tool pins).
- It runs the package's Python gates and unit test.
- It runs `make test-template-web` (a new web package installs the two tarballs and passes `lint-web`) and C26 (`npm run test:external`, including Chromium and WebKit).
Each step is a separate Make command, so any failed gate fails the job. The local run passes. The Python and JS Playwright pins are recorded separately in `release.md`, and `release.md` also defines how maintained packages move to a new policy release. Live tests that need a separate runner: real Kitty E2E (`pytest -m kitty`, a macOS runner with Kitty and remote control) and the live harness and adapters checks (a configured live environment). Neither runs in CI.
Open: a remote CI result. It needs a push, which is the user's decision. The workflow was checked only as parsed YAML and by running its Make targets locally.

## Work record — independent web package

Date: 2026-09-14. Source: uncommitted working tree based on `6a9e497`. Platform: macOS, Node 22.22.3, npm 10.9.8, Python 3.12.1. No daemon restart or application route change was made.

Implemented:

- `packages/extension-api-web/` exports public wire aliases generated from the Python SDK, view lifecycle types, and the generic view host. It has no runtime dependency on Svelte or private dashboard code.
- The loader validates same-origin owner and digest asset paths. Each view has its own Shadow DOM, immutable data copy, narrow client facade, and abort signal. It serializes updates and cleanup. A late mount is disposed once. Failed views have a local fallback. Same-page JavaScript is trusted code, not a security sandbox.
- `tests/extension_web/fixture/` owns a Svelte runtime, CSS, diff/tree/thread prototype views, a unit test, and browser cases. The test builds the small generic host once, builds two feature versions, and checks that the host bytes do not change.
- `packages/dev-tools-web/` supplies shared ESLint, TypeScript, Prettier, and coverage exports. The formatter imports its own Svelte plugin, so editable links and artifact installs both work. Local host development uses standard npm file links. External tests use real tarballs.
- `Makefile` includes SDK lint, type, format, generated-type drift, and coverage checks. The new Python schema exporter is also in the Python quality paths. CI setup installs the shared tooling first.

Checks:

| Check | Result |
| --- | --- |
| `make lint-frontend test-frontend` | Passed. Dashboard: 114 tests in 27 files. SDK: 22 tests in four files. |
| Dashboard coverage | Statements 77.93%, branches 63.63%, functions 85.34%, lines 81.17%. Thresholds are unchanged. |
| SDK coverage | Statements 86.80%, branches 78.08%, functions 88.57%, lines 87.05%. Uses the same thresholds. |
| Effective configuration comparison | ESLint rules, globals, parser options, and both TypeScript compiler settings match the stored pre-extraction values after editable installation. |
| `npm run test:external -- /tmp/baqylau-extension-web.qUQfGt` from the SDK | Passed. Fresh install; formatting; Svelte and TypeScript checks; ESLint and Knip; one unit test; Chromium and WebKit, two passed. |
| External install location | `/var/folders/0h/ksv1c8290cg9mfkkfsghdpkc0000gq/T/baqylau-external-view-QKBOas`. No sibling host import alias. |
| npm dependency audit | SDK, tools, and external fixture: no findings in their checked installs. Dashboard: two existing high-severity findings in unchanged `js-yaml` and `@redocly/openapi-core` versions. No automatic dependency update was made. |

Artifacts from this check:

| Artifact | SHA-256 |
| --- | --- |
| `/tmp/baqylau-extension-web.qUQfGt/baqylau-dev-tools-0.1.0-alpha.1.tgz` | `c5b9086c1588369b519a44364f67e986f8189cf3cbb5827b51ad9a2c20784f9d` |
| `/tmp/baqylau-extension-web.qUQfGt/baqylau-extension-api-0.1.0-alpha.1.tgz` | `e6681aa0e8a5798e00760c882cd4937fc973d8d9bac1f8918c5f3b351b93ca08` |

These artifacts contain the tested source before later documentation edits. They are local draft artifacts, not published releases. The external package proves independent builds and view lifecycle behavior in a small host. It does not complete main-dashboard E2E, C26, C27, or any phase. The browser client currently offers peer directory reads only. Complete queries, commands, subscriptions, settings routes, and main-dashboard integration remain open.

Next action: Finish the remaining P01 contracts and terminal block prototype. Complete the shared Python policy, test profiles, negative parity cases, architecture runner, and release metadata before closing P02.

## Work record — generated terminal models and build inputs

Date: 2026-09-14. The browser SDK now exports terminal wire types from the Python models. Six additional asset tests check owner syntax and credential-bearing URLs. The latest SDK unit run has 28 passing tests and still uses the common coverage thresholds.

`dashboard/frontend_build_inputs.py` now includes the shared compiler and build configuration in the source digest. It also includes the dashboard's `.npmrc`. Six tests prove that a shared input change invalidates the build stamp. `tests/test_frontend_policy_inputs.py` has strict mypy settings. The full Python suite passed after the required frontend rebuild.

Fresh artifacts used by the latest external test:

| Artifact | SHA-256 |
| --- | --- |
| `/tmp/baqylau-extension-terminal.3xkMIL/baqylau-dev-tools-0.1.0-alpha.1.tgz` | `5a35eb913daf861a6108c1fdc6ed1a7c7a61c4bda69e37f0aa52d734c35c284d` |
| `/tmp/baqylau-extension-terminal.3xkMIL/baqylau-extension-api-0.1.0-alpha.1.tgz` | `3d860979b9b79af5b8c794a5ada2153f06c8f476665c5278354942053a84c670` |

`npm run test:external -- /tmp/baqylau-extension-terminal.3xkMIL` passed from the SDK directory. It installed the artifacts in `/var/folders/0h/ksv1c8290cg9mfkkfsghdpkc0000gq/T/baqylau-external-view-hKhyDv`. Formatting, types, ESLint, Knip, one unit case, and both Chromium and WebKit cases passed. The package install reported no dependency audit findings. The two browser cases completed in 5.4 seconds on this local run; this is not a performance guarantee.

The independent build and cleanup results still apply only to the small test host. The main dashboard and Kitty integrations, complete shared Python and web policy runners, policy violation tests, and all P02 release criteria remain open. Plan record checks pass for all ten phases and 53 subtasks.

## Work record — generated operation models

Date: 2026-09-14. Python query and command models now generate the browser wire declarations. The SDK exports query snapshots, page selections, command bindings, outcome variants, cancellation, reconciliation, and observation candidates. These are worker contracts. Browser job acceptance and runtime authority must use separate host API models. The browser client still offers directory reads only.

`make test-extension-web` and the SDK lint command pass. The generated-type drift check passes. All 28 unit cases pass, with unchanged coverage: statements 87.5%, branches 80%, functions 88.57%, and lines 87.76%. No common rule or threshold was reduced.

Fresh artifacts used by the external install:

| Artifact | SHA-256 |
| --- | --- |
| `/tmp/baqylau-extension-operations.FRxJm7/baqylau-dev-tools-0.1.0-alpha.1.tgz` | `5a35eb913daf861a6108c1fdc6ed1a7c7a61c4bda69e37f0aa52d734c35c284d` |
| `/tmp/baqylau-extension-operations.FRxJm7/baqylau-extension-api-0.1.0-alpha.1.tgz` | `a9e7c3a85368a7a1f22f239a4299a065e028ca34dbc3a62c5291b5555311f9de` |

`npm run test:external -- /tmp/baqylau-extension-operations.FRxJm7` passed from the SDK directory. It installed the artifacts in `/var/folders/0h/ksv1c8290cg9mfkkfsghdpkc0000gq/T/baqylau-external-view-HJNE62`. Formatting, types, ESLint, Knip, one unit case, and both Chromium and WebKit cases passed. The install reported no dependency audit findings. The browser run took 5.3 seconds locally; this is not a performance guarantee. The artifacts precede the later README clarification about browser authority.

The main dashboard does not mount these modules yet. Shared Python policy, complete web test profiles, negative policy tests, package templates, and release metadata remain required. This record does not complete P02 or replace any host conformance test.

## Work record — shared Python policy

Date: 2026-09-14. Scope: P02-T02 is complete. P02-T05 now has a Python gate runner but remains in progress. This continues the full P01–P10 goal. It does not change the live daemon, database, runtime extension loading, or feature frontends.

Implemented:

- `packages/dev-tools/` builds the typed `baqylau-dev` package. Setuptools reads its tool dependencies from the same packaged requirements resource used by the parity report. The package imports no private host or extension SDK module.
- Common Ruff, mypy, and WPS rules are packaged resources. Vulture uses the existing product-only scan and framework decorator flags. Root paths are explicit profile input. Tests remain outside the Vulture product scan.
- Existing lint pins are unchanged: Ruff 0.15.21, WPS 1.7.1, Vulture 2.16, and mypy 2.3.0. The installed Flake8 7.3.0 version is now an explicit pin. The previously ranged pytest, timeout, and xdist requirements now use their existing installed versions: 9.1.1, 2.4.0, and 3.8.0. Python and JS Playwright pins are unchanged.
- `quality/ruff-host.toml` and `quality/mypy-host.ini` retain host path settings, runtime annotation readers, and the current test migration exceptions. External tests are strict. `baqylau-dev.toml` selects the host roots and exact policy release.
- Root `ruff.toml`, `mypy.ini`, and `setup.cfg` are generated editor files. `make policy-generate` updates them. The runner checks their bytes before a gate. Explicit config arguments prevent a tool from choosing a weaker local file. Ruff uses its standard inheritance; ConfigParser assembles the INI settings.
- A path check found that inherited Ruff test patterns were relative to the policy resource. The generator now places shared path patterns in the project configuration. Host effective settings, an external path with spaces, and a renamed external test directory all pass.
- The host's `typecheck`, `deadcode`, `wemake`, and final Ruff Make targets use the installed runner. `lint` keeps frontend checks before types, dead code, WPS, and Ruff, with policy validation first. Existing test and live E2E Make workflows remain intact. The runner's host unit command explicitly excludes live E2E and real Kitty tests.
- The runner reports a digest over the policy resources and its Python source. It checks all eight exact tool versions. It does not install tools while a gate runs. CI invokes `make policy-check` after dependency installation.
- `tests/dev_tools/` contains baseline configuration fixtures and 48 cases. Tests compare every original mypy option, WPS option, and effective Ruff input. Negative cases cover incorrect return types, dead product code, WPS names, unused imports, failed tests, weak local configs, changed versions, host-only exemptions, missing paths, source links, output link escapes, and generated file drift.

Verification:

| Check | Result |
| --- | --- |
| `make policy-check` | Passed. Host generated files and all eight installed pins match. |
| `make typecheck` | Passed on 2,259 source files, including policy source and strict policy tests. |
| `.venv/bin/python -m pytest -q tests/dev_tools` | 48 passed; two existing pytest plugin rewrite warnings. |
| Policy tests plus existing type-config and API architecture checks | 57 passed. Existing exemption checks remain active. |
| Full non-live Python suite with six workers | 2,133 passed; 20 existing pytest plugin rewrite warnings. This adds 48 policy cases to the previous 2,085-case run. |
| Root Ruff and scoped WPS for both SDKs, host extension code, and their tests | Passed. No rule threshold or private-code exemption was changed. |
| `make lint-frontend test-frontend` | Passed. Dashboard: 114 tests. Web SDK: 28 tests. Types, formatting, ESLint, Knip, generated-wire checks, and coverage gates pass. |
| `make lint` | Not passed. Policy, frontend, and type gates pass; Vulture reports the same 20 extension components without application callers. |
| `make wemake` | Not passed. The same six findings remain in unrelated Codex control and test changes. Those files were not edited. |
| Host and clean environment `pip check` | Passed. |
| `git diff --check` | Passed after the generator removed INI trailing whitespace. |

Distribution evidence:

- Wheel: `/tmp/baqylau-python-policy.LDYgdY/baqylau_dev-0.1.0a1-py3-none-any.whl`.
- Wheel SHA-256: `234d992e0c0dd0b159b4be700c68bddbc814fb9eae0985abf50740eb565ae05f`.
- Policy digest in both the host and clean install: `c93755ac24b5fe28be134f95e9f397d731262ac5a5f4cdbaf7744933913ab707`.
- A fresh environment at `/tmp/baqylau-python-policy.LDYgdY/venv` installs that wheel and its dependencies. Python `-I` imports all nine submodules without finding `app`, `domain`, or `baqylau_extension_api`. The wheel contains only `baqylau_dev`, its five policy resources, typing marker, and distribution metadata.
- All 48 policy tests also pass when their child gate commands use the fresh installed interpreter. The parent test runner remains in the host test environment. The external fixture's source, tests, and generated configs live outside the checkout. This proves standalone tool use and deliberate gate failure, not dynamic extension entry points or product E2E.

Limits: The external fixture is an ordinary Python quality package. It does not supply the complete extension backend and frontend template. The Vulture adapter for verified manifest and protocol entries, reusable architecture checks, full source inventory, browser and E2E dispatch, remote CI results, and release compatibility proof remain open. P02 and the full extension system are not complete. The dashboard dependency install still reports two existing high-severity audit findings; no dependency upgrade was made in this extraction.

Next action: Implement P02-T04's shared architecture and verified dynamic-entry checks. Then complete the package template and gate dispatch in P02-T05, and the remaining frontend policy and release checks. Continue the unfinished P01 capability contracts and application integration. The adapters and Git packages in P09 and P10 remain required.

## Work record — verified Python extension entries

Date: 2026-09-15. Source: uncommitted working tree based on `6a9e497`. Platform: macOS, Python 3.12.1. Scope: the first Python subset of P02-T04. This task and P02 remain in progress. No daemon, database, application route, or feature frontend was changed.

Implemented:

- `baqylau_dev.signatures` owns the protocol parameter rule. Host architecture tests import the same functions. The rule preserves positional-only, keyword-only, and variadic argument kinds. Existing host protocol and stale-exemption tests still run.
- Source inventory uses explicit product and test roots. It rejects duplicate module names, unselected Python files, and source files linked outside the package. It resolves package modules, static aliases, relative imports, and concrete inheritance without importing feature code.
- The Python extension profile now requires a backend in `extension.json`. The public SDK validates manifest structure, registered schemas, owner references, and API range. New file checks resolve the declared module and factory, E2E paths, and asset digests inside the package.
- The tools package now depends on `baqylau-extension-api==0.1.0a1`. This replaces the previous policy-only package's lack of an SDK dependency. The checker uses the installed public models instead of defining a second manifest parser. It still imports no private host module and does not load feature code.
- Initial import rules reject private host roots, worker runtime imports in product code, direct storage clients, and undeclared third-party or peer modules. These static checks do not provide an OS security boundary.
- The checker reads supported SDK protocol shapes from `ExtensionCapabilities`. A concrete implementation must declare its SDK protocol and match its method parameters. A generated assignment to `ExtensionFactory` lets strict mypy check the entry's argument and return types. The checker never executes that generated file.
- The external `architecture`, `types`, `deadcode`, and `lint` gates use those checks. Dead-code checks require valid types. Vulture still scans product files without test callers. Its adapter removes findings only for a verified file, line, and declaration name. No name-wide allowlist was added. Each run recomputes the entries, so an old factory loses its exception when the manifest changes.
- `.baqylau-dev/extension_factory_check.py` is the one generated type probe. It is included in mypy and excluded from Ruff by its exact path. The shared rule resources and tool pins did not change. The host retains its existing lint order and SDK exemptions.
- The external fixture now owns backend source, a manifest, a behavior test, and `tests/e2e/test_worker.py`. The worker case starts the installed SDK in a child, loads the declared factory, and checks activation. Its source passes the same tools as product packages. This proves worker behavior only, not main-dashboard, Kitty, or live-service behavior.
- There are 31 new policy cases, for 79 total. Negative cases cover invalid factories, wrong protocol declarations and signatures, private imports, direct storage imports, missing E2E declarations and files, source and file escapes, API and policy mismatch, and changed asset bytes. A same-name unused method still fails Vulture. A changed manifest makes the old factory fail as dead code. A module-level import trap proves that architecture checks do not execute feature source.

Verification:

| Check | Result |
| --- | --- |
| Full non-live Python suite, six workers | 2,164 passed; 20 existing pytest plugin rewrite warnings. |
| `make typecheck` | Passed on 2,273 source files. |
| Root Ruff | Passed. |
| Wemake for the tools package and its tests | Passed. No threshold was reduced. |
| `make lint-frontend test-frontend` | Passed. Dashboard: 114 tests. Web SDK: 28 tests. Coverage and thresholds are unchanged. |
| `make policy-check` | Passed. Generated host configs and all eight tool pins match. |
| `make deadcode` | Not passed. The same 20 SDK and mapper components still need application callers. No new finding was added. |
| `make wemake` | Not passed. The same six findings remain in unrelated Codex control and test changes. Those files were not edited. |
| Host and clean environment `pip check` | Passed. |
| `git diff --check` | Passed. |

Distribution evidence:

- Final tools wheel: `/tmp/baqylau-extension-policy.JUkCim/release/baqylau_dev-0.1.0a1-py3-none-any.whl`.
- Tools wheel SHA-256: `1c94488bada6b035dfcc426b91ac22a6c17a2d23e4d60f6d460088c2adf9a003`.
- SDK wheel: `/tmp/baqylau-extension-policy.JUkCim/baqylau_extension_api-0.1.0a1-py3-none-any.whl`.
- SDK wheel SHA-256: `a7d827f4d8c1d9a65e21bcac9affb922e3a7f93133f3714ea45dc3fd789bb76a`. SDK source was not changed in this step.
- Policy digest in the host and clean install: `b2c10030a1d1b2407bb2407d85a852d90c3fdd15992bf764116d64e1a69dce72`.
- Clean environment: `/tmp/baqylau-extension-policy.JUkCim/venv`. Python isolated mode imports all 18 tools submodules without finding `app`, `domain`, or the fixture backend. It uses installed SDK and policy wheels, not editable links to those sources. These are local draft artifacts, not published releases.
- All 79 policy tests also pass with their child gate commands set to the clean installed interpreter. The parent pytest runner remains in the host test environment. External package source, generated checks, behavior tests, and the worker case run outside the checkout. The worker case uses the clean installed SDK.

Remaining P02-T04 work: complete extension-defined protocol checks and default methods; peer public contract package declarations; typed document and schema boundaries; source and command I/O placement; complete test surface and harness coverage checks; nested configuration reporting; frontend manifest entries and Knip integration. Existing static declaration checks are not a complete dynamic import analysis. Do not mark P02-T04 complete from this subset.

Next action: finish those architecture boundaries and their failure fixtures. Then complete the full package template and browser/E2E dispatch, remaining frontend policy profiles, and release checks. P01's observer, migration, and host service contracts remain open. P03–P10, including the main settings page, Kitty integration, adapters, and Git packages, remain required.
