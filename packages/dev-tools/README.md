# Baqylau Python tools

This draft package supplies the common Python quality policy. It uses the existing Ruff, mypy, Wemake, Vulture, and pytest tools. The host keeps its path and legacy settings in `quality/`. External profiles do not inherit those settings.

The package does not load feature backends or install tools at gate time. It now depends on the matching public Python SDK to validate extension manifests and protocol declarations. It imports no private host code. Complete schema-boundary checks, peer contract rules, browser dispatch, and release E2E dispatch remain open.

## Host commands

```sh
make policy-check
make policy-generate
make typecheck
make deadcode
make wemake
make lint
```

`policy-check` rejects changed tool versions and generated configuration drift. It reports the policy version, digest, and tool versions. `policy-generate` is the explicit command to update root editor configuration after a policy or host overlay change. It does not install dependencies.

The common rules live in `baqylau_dev/policy/`. The wheel includes those files. Exact lint pins are unchanged. The previously ranged pytest, timeout, and xdist requirements now use the versions installed at extraction: 9.1.1, 2.4.0, and 3.8.0. Flake8 is also pinned at the existing 7.3.0 version. Host-only mypy sections and runtime annotation readers live in the host's `quality/` directory, outside the policy wheel.

The root `ruff.toml`, `mypy.ini`, and `setup.cfg` are generated files. Ruff uses its [standard `extend` configuration](https://docs.astral.sh/ruff/configuration/). INI files use the standard `ConfigParser`. All gate commands pass an explicit config path. Shared test path patterns are placed in the project config so that installed-resource paths cannot change their meaning.

## External Python profile

Install a selected `baqylau-dev` wheel in the package's development environment. Then add `baqylau-dev.toml`:

```toml
policy_version = "0.1.0a1"
profile = "extension"
source_roots = ["src"]
test_roots = ["tests"]
```

Use the installed command:

```sh
python -m baqylau_dev check --gate lint
python -m baqylau_dev check --gate unit
python -m baqylau_dev report
```

Individual gates are `parity`, `architecture`, `types`, `deadcode`, `wemake`, `ruff`, and `unit`. For an external backend, `lint` checks package architecture before types, dead code, WPS, and Ruff. The `architecture` gate also runs strict types. The `deadcode` gate requires architecture and types before it accepts dynamic exceptions. A failed child command stops the gate and returns a nonzero exit code. These commands do not replace the complete frontend or E2E runner. The host retains its existing lint order; its architecture gate runs the existing architecture tests.

External generated files stay in `.baqylau-dev/`; ignore that directory in version control. Product and test roots must exist inside the project and remain separate. All Python tests are strict. Local root Ruff, mypy, Flake8, and Vulture rule overrides are rejected. Host-only exemptions and extra design exclusions are not accepted in an external profile. Source and test roots can have different names, including paths with spaces.

The Python extension profile now requires `extension.json` with a backend and E2E entries. The SDK checks schema registrations, ownership references, and API compatibility. The package checks factory and module declarations, local E2E paths, asset bytes, source inventory, private host imports, direct storage imports, and undeclared dependencies. These are static quality checks, not an OS security boundary. Checking an E2E path does not prove that the test covers its declared feature.

The host and extension checks share protocol parameter rules. The extension checker reads supported protocol shapes from the installed SDK. It resolves static aliases, relative imports, and concrete base classes. A generated type assignment checks the factory against `ExtensionFactory`, including its return type. The generated file is type-checked and is excluded from Ruff by its exact path. Other extension source remains subject to all common rules.

The dead-code scan excludes test callers. Vulture still performs the scan. The adapter filters only the exact file, line, and name of a verified factory or protocol method. It does not add a name-wide allowlist. Each run rebuilds these exceptions from current declarations, so an old factory loses its exception after the manifest changes.

The external fixture now owns a backend, manifest, behavior test, and worker E2E case. Its worker test loads the installed SDK in a child process, loads the package factory, and calls activation. It needs no host source, running dashboard, Kitty, database, or external service. This is a worker check, not a complete extension release test.

P02-T04 remains open. Local extension-defined protocols and their default methods need the full shared rule set. Complete peer-public-contract declarations, typed document boundaries, source and command I/O placement, test surface coverage, nested configuration reporting, and frontend dynamic entries also remain required. Web-only packages must use the frontend runner; this Python profile does not claim to check that surface.

The digest covers policy resource bytes and the runner's Python source, not their installed paths. Host overlay and project root selection are separate inputs. This report is not a signed package attestation or proof that all extension release gates ran.
