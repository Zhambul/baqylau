# Baqylau extension API

This is a draft SDK. It does not load extensions into the daemon. The current API version is `0.1.0a1`. Do not treat its contracts as stable API version 1.

The package contains:

- `ExtensionPlugin`, its frozen capability group, and a typed factory protocol.
- Lifecycle, source, translation, raw and canonical transform, projection, projection transform, observer, query, command, migration, terminal presentation, and peer directory protocols.
- Strict, frozen wire models for scopes, documents, candidate facts, transforms, and revisions.
- A closed public model for all 43 core event types, with typed nested content and exact decimal costs.
- Pure raw and canonical operation validation and ordering, with stable derived IDs and protected source fields.
- Bounded, digest-checked raw content snapshots. Workers receive exact bytes without reading host files.
- Data-only manifests for backend-only, web-only, and combined packages, plus schema, view, settings, service, and E2E declarations.
- API compatibility and active-set validation with dependency order, service versions, and exclusive view checks.
- JSON Schema 2020-12 validation with a registered local schema set. Schema resolution does not fetch network resources.
- A JSON-RPC worker prototype, typed process proxies, peer callbacks, request deadlines, and separate bounded execution lanes.
- Typed terminal text, sections, tables, file trees, diffs, status, and lists. Complete-result checks cover identity, layout bounds, control text, and registered command actions.
- Query snapshots and page cursors tied to owner, operation, scope, arguments, settings revision, and runtime revision.
- Command execution, cancellation, and reconciliation with exact attempt bindings and distinct success, failure, canceled, and uncertain outcomes.
- Source plans, file watch requests, known deadlines, bounded reads, exact resume positions, and source release results.
- Pure translation from captured bytes and decoder state, explicit input verdicts, and stable scoped fact identities.
- Pure record-key selection and projection, typed record states and writes, ordered extension feed rows, and stable feed identities.
- Closed core session and actor models and all 25 core feed bodies. Projection transforms can change display data, suppress feed rows, and add rows with stable IDs.
- Pure settings and record migrations with explicit schema paths, candidate bindings, complete batch checks, and typed failure results.
- Post-commit observer execution, cancellation, and reconciliation, with explicit effect policy and cause-linked output observations.
- Declared peer service lookup and reads, with host-issued call authority, scope checks, fixed deadlines, and cycle limits.

The SDK does not import the host application. The host imports the same protocol definitions through `extensions/contract.py`. An external backend imports `baqylau_extension_api` only, not `extensions` or private host modules.

## Install and check

From the host repository root:

```sh
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
make test-extension-api
make lint
```

The root Ruff, mypy, Wemake, and Vulture checks include this package. Its tests use strict mypy rules, not the legacy test exemptions. `vulture_extension_api.py` lists public entry points that external packages or wire readers use. A test checks that each listed name exists in SDK source. It does not yet check for unused exemptions.

`baqylau-dev` now supplies the common Python policy and lint/unit runner from `packages/dev-tools/`. The host consumes the same installed package. Initial external architecture checks and verified factory and SDK method entries are implemented. Complete architecture checks, frontend dispatch, and the full extension template remain P02 work.

## Writing an extension

See `docs/extensions/authoring.md` in the host repository for the manifest,
the backend protocols, web and Kitty views, cooperation, settings, migrations,
and tests, and `examples/hello-extension/` for a complete small package.

## Current limits

This is a draft API version. The host runs each backend in a private worker
process with an offline, hash-locked environment. The worker is a failure
boundary, not an OS sandbox. Pure calls have a 5-second deadline and other
calls 30 seconds; after 5 consecutive failures the host disables the
extension. See `docs/extensions/performance.md` for the measured limits and
`docs/extensions/release.md` for the release state.
