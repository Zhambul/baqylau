# Shared tools and quality policy

Status: in_progress

## Requirement

Every extension uses the same tool versions, shared rules, and required check types as the main repository for its declared surfaces. The host and extensions must consume one policy source. Copying the current configuration into extension templates is insufficient because the copies would diverge.

A backend-only extension runs all Python checks. A web-only extension runs all web checks. An extension with both runs both. Tests and terminal layout code receive the same applicable checks. A package cannot claim a missing surface only to avoid checking source files that it contains.

## Current baseline

The following values were read from the repository on 2026-09-14. Re-read them when P02 starts:

| Area | Current tools and rules | Current source |
| --- | --- | --- |
| Python types | Python 3.12; mypy 2.3.0; global strict mode; unused-ignore and unreachable checks | `mypy.ini`, `requirements-dev.txt` |
| Python lint | Ruff 0.15.21; `select = ["ALL"]`; preview enabled; line length 120 | `ruff.toml` |
| Python design | wemake-python-styleguide 1.7.1 through Flake8; all WPS rules | `setup.cfg` |
| Python dead code | Vulture 2.16; product paths; explicit dynamic roots | `Makefile`, `vulture_allowlist.py` |
| Python tests | pytest, timeout, xdist; replay; separate live harness and Kitty layers | `pytest.ini`, `requirements-dev.txt`, `Makefile` |
| Web types | TypeScript 5.9.3; svelte-check 4.7.6; strict types, exact optional properties, checked indexed access | `dashboard/frontend/tsconfig*.json` |
| Web lint | ESLint 10.9.0; strict and stylistic typed rules; Svelte rules; zero warnings | `dashboard/frontend/eslint.config.js` |
| Web dead code | Knip 6.32.2; explicit entry points and generated-file exclusion | `dashboard/frontend/knip.json` |
| Formatting | Prettier 3.9.6; Svelte plugin 4.1.1; single quotes and trailing commas | `packages/dev-tools-web/prettier.mjs`; selected by dashboard `package.json` |
| Web tests | Vitest 4.1.11; V8 coverage; Playwright 1.62.1 in the JS package | `dashboard/frontend/package.json` |
| Coverage | Branches 55%, functions 75%, lines 75%, statements 70%; selected behavior modules | `dashboard/frontend/vite.config.ts` |
| Runtime for web tools | Node 22.22.3 in CI; npm 10.9.8 | `.github/workflows/test.yml` |
| Architecture | Layer direction, typed JSON, explicit protocol implementation, owned tables, client boundaries | `tests/architecture_*.py`, `tests/test_architecture_*.py` |

The Python Playwright package is currently 1.62.0; the JS package is 1.62.1. Treat them as distinct pinned clients in the first extraction. Keep their browser installs separate where required. Do not change an existing pin as an accidental part of policy packaging.

There is no current Python coverage percentage gate. Do not describe one as an existing requirement. New extension tests are strict from their first version. Do not inherit the main repository's legacy `mypy-tests.*` migration exemptions.

## One policy source

Current Python implementation: `packages/dev-tools/` builds the installed `baqylau-dev` policy and runner. It owns the common Ruff, strict mypy, WPS, and Vulture gate behavior and the exact tool pins. The host selects roots in `baqylau-dev.toml`; host-only overrides stay in `quality/`. `make policy-generate` writes the root editor configurations, and `make policy-check` rejects drift before lint. External profiles generate local files under `.baqylau-dev/` and do not inherit host test exceptions. See [the package guide](../../packages/dev-tools/README.md) and [P02 evidence](phases/02-sdk-and-quality.md#work-record--shared-python-policy).

The main `make lint` includes both Python packages in strict mypy, Vulture, Wemake, and Ruff checks. `tests/extension_api/` and `tests/dev_tools/` are strict. `vulture_extension_api.py` names exact public SDK methods and wire fields, not private helpers. A test checks that these names exist. P02-T04 now provides verified external factory and SDK method locations, common protocol parameter checks, manifest file checks, and initial import and source inventory checks. The full architecture rule set remains open. No common lint rule was disabled for the SDK or tools package.

Current frontend subset: `@baqylau/dev-tools` owns the ESLint, browser and Node TypeScript, Prettier, and coverage exports. Both the dashboard and the web SDK consume them. The external Svelte fixture installs the same exports from a tarball. The root gates check both packages. Effective dashboard rule and compiler settings match their earlier values. Complete frontend test and dead-code profiles, pin validation, and deliberate frontend policy violation fixtures remain open. The shared exports are not yet the complete extension quality runner.

Create shared tooling sources under proposed `packages/dev-tools/` and `packages/dev-tools-web/`. Publish `baqylau-dev` and `@baqylau/dev-tools` from the same quality policy release. Standard package build tools produce the artifacts.

Keep a policy manifest with the exact tool pins, common rule resources, profile names, and policy digest. Host CI checks the extracted policy against the original effective configuration before removing duplicated values. External CI checks that its locked tools match the declared policy release.

Both the host and an extension may select a profile. Profiles change source roots, entry points, platform-specific fixtures, generated files, and narrowly documented legacy host exceptions. They do not lower the common rule strength.

Use existing configuration mechanisms:

| Tool | Shared configuration method | Package-specific information |
| --- | --- | --- |
| Ruff | Shared base resource; thin config with `extend`; runner resolves the installed resource | Source roots, generated directories, explicit import roots |
| mypy | Shared base resource and a small generated complete config using standard INI parsing | Package roots and documented host-only legacy sections |
| Flake8/WPS | Shared base resource; standard INI assembly for paths | Build and environment exclusions |
| Vulture | Shared runner and flags | Product roots and manifest-declared dynamic entry points |
| ESLint | Export a typed config factory from the npm policy package | Root directory and TypeScript project paths |
| TypeScript | Shared base configs through `extends` | `include`, `exclude`, types, and source project references |
| Prettier | Import the shared configuration export | File roots and generated-output exclusions |
| Knip | Shared config factory | Entry modules, manifest exports, and build assets |
| Vitest | Shared test defaults and coverage thresholds | Test setup and behavior module inventory |
| Playwright | Shared assertions and browser profiles | Private host fixture, tests, and platform snapshots |

[Ruff supports explicit configuration extension](https://docs.astral.sh/ruff/configuration/). [ESLint supports shared configuration packages](https://eslint.org/docs/latest/extend/shareable-configs). [TypeScript resolves inherited configuration paths relative to their source file](https://www.typescriptlang.org/tsconfig/extends.html). Test installed-package path resolution; do not assume a path from the host checkout works in an extension.

Keep generated local configuration under an ignored package-local directory. It may refer to installed resource paths. It must not contain a developer-specific path in a committed file. The runner uses tool CLI options and standard parsers; it does not implement another linter.

## Dynamic entry points and false dead code

Factories, command methods, renderers, and web mount exports are called by the host. They must be visible to dead-code checks through manifest roots and protocol declarations.

Feed Vulture only actual backend product modules plus explicit framework roots. Do not add tests to its product scan to make unused product code appear used. Feed Knip the web module entry and manifest-declared exports. Check that every declared root exists and is used by a valid contribution.

Keep narrow exemptions with a reason and a stale-exemption check. Do not add broad ignores for `extensions/**`, all protocols, all factories, or all generated-looking names.

Current Python implementation: the external runner validates the manifest and explicit SDK implementations, then checks a generated assignment to `ExtensionFactory` with strict mypy. Vulture scans product files without tests. The adapter removes findings only at the exact verified file, line, and name. A test proves that an unrelated `activate` method still fails. Another changes the manifest entry and proves that the former factory becomes dead code. The host's existing SDK public-symbol exemptions remain separate and unchanged.

## Extension architecture checks

Package reusable checks with an explicit root argument. Existing checks often infer the main repository from `__file__`; they need a root and package inventory before external reuse.

Required checks include:

- Concrete implementations explicitly declare their protocol and have matching methods.
- Public models do not import host services or feature packages.
- External feature code imports only the published SDK, its own modules, declared third-party packages, and declared public contract packages.
- No imports from host `app`, `engine`, `repository.impl`, private dashboard modules, or another feature package's private modules.
- Extension storage uses host record methods. Source and command I/O stays in declared capability modules.
- Owned documents use typed models and codecs. Opaque JSON is restricted to the schema boundary.
- Every manifest entry and schema resolves within the package.
- Every declared feature has E2E coverage for the supported surfaces and harnesses.
- Host production files do not contain adapters-specific or Git-extension-specific branches.

Extend the current architecture checks where possible. Keep root and profile selection separate from rule logic. The extension profile uses the same design rules while accounting for its package ownership.

## Developer commands

These commands are proposed; P02 implements them:

```sh
make lint
make test
make e2e
```

An extension's small Makefile forwards these commands to the installed shared tooling. Its web scripts retain `check`, `lint`, `format:check`, `test:coverage`, and `test:browser` so developers use familiar commands.

`make lint` checks policy parity, frontend lint where applicable, strict Python types, dead code, WPS, and Ruff. `make test` runs frontend formatting and type checks, unit coverage, isolated browser checks, Python behavior, and architecture checks for the declared surfaces. `make e2e` runs the declared replay, browser, real Kitty, and live integration scenarios.

Keep the current host gate order during extraction. The runner must return a nonzero result when any required check fails. Test each gate with a deliberately invalid fixture package. Do not rely on a large successful run to prove that every gate was called.

## CI and releases

The host and extension CI jobs install locked dependencies, check dependency consistency, then use the same policy commands. Linux and macOS run the applicable existing host checks. Platform-specific screenshots stay separate. Real Kitty and token-spending tests run only on a suitable configured runner and are recorded as distinct release evidence.

An extension release declares the host API range and quality policy release it supports. A CI report records package digest, host version, policy digest, tool versions, executed scenarios, and skipped scenarios with reasons. Normal runtime activation checks compatibility; it does not run linters or tests on the user's machine.

Update the policy as an explicit release. The main repository and maintained extension packages update their pins and checks together for the target release. Package-local rule suppression cannot silently override the shared policy; the parity check reports it.
