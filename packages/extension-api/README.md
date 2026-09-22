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

## Current limits

### Draft runtime dependency declaration

A backend that uses host environment preparation declares its own lock file and dependency wheels:

```json
{
  "backend": {
    "module": "example.backend",
    "factory": "build_extension",
    "environment": {
      "requirements": "requirements.lock",
      "wheelhouse": "wheels"
    }
  }
}
```

Both paths are relative to the built package. The wheelhouse must contain the selected SDK release and all runtime dependencies for the host Python and platform. Requirements must pin named packages with `==` and full SHA-256 hashes. Extras, markers, multiple hashes, comments, and line continuations are accepted. Direct sources, URLs, editable installs, nested requirements, and installer directives are not accepted. The host installs offline from the checked package copy with `uv` and verifies the private Python and SDK identity without importing the feature.

Earlier draft manifests can omit `environment` and remain discoverable. They cannot pass host environment preparation. The daemon does not yet start these prepared workers. Test wheel repacking is not the extension release build process; the complete package template and release runner remain open.

The installed worker entry now requires `--package-directory` together with `--rpc-fd` and `--runtime-revision`. It selects a flat or `src/` backend path after declaration checks. It rejects source links, ambiguous entry files, and backend names that shadow installed modules. Feature code does not need a custom `-c` bootstrap or a host source path. The host process adapter starts this entry with private Python and a separate RPC socket; logs use stdout and stderr. This managed process adapter has real tests, but normal daemon enable/disable does not call it yet.

### Application integration limits

Wire validation checks types and declared model constraints. Raw and canonical operation checks validate input scope, ordering, output identities, content, schema ownership, and protected source fields. These are local checks against the supplied immutable request. They do not prove the worker's authority, stored cause references, or current host revisions. The host processing boundary must add those checks before accepting worker output.

`manifest.validation.validate_manifest()` checks package-local declarations and exact schema references. `manifest.activation.activation_order()` checks a proposed active set. Neither function reads package files, validates installed byte digests, starts a worker, or switches the daemon runtime. All 12 declared backend capabilities now have draft Python protocols and worker adapters. Most narrow host-service protocols are still pending.

Repository paths must have a normalized absolute POSIX form. Asset paths must have normalized package-relative syntax. The host still must resolve symlinks, check installed files, and verify the worktree and Git directory. The core mapper exists in the host's `extensions/mapper/` package; the SDK has no host imports. Runtime callers are not implemented.

The worker prototype uses `jsonrpcpeer==0.2.0` on an inherited local socket. It supports lifecycle, sources, translation, raw transforms, canonical transforms, projection, projection transforms, observers, queries, commands, migrations, and terminal presentation. It checks package declarations before imports, then checks the returned identity and capabilities. Logs do not use the RPC socket. The worker is not an OS sandbox.

The scheduler has one pure thread, four live threads, and one control thread. Each lane permits at most 16 running or queued calls. Cancellation uses the control lane, so a full live lane cannot prevent a stop request. This does not make arbitrary feature code interruptible.

`ExtensionHostServices.service_access` supplies `ExtensionServiceAccess` only when the package declares consumed services. `resolve_service()` returns public query definitions and the current package, service, and runtime versions. It can run during factory preparation without query authority. Private query definitions and peer settings values are not returned. Missing, disabled, incompatible, absent, and stale services have explicit results.

`query_service()` requires an active grant from `HostCallLedger`. The host binds the caller to its worker connection. An opaque call ID crosses RPC and thread boundaries, but it is not authority by itself. The host checks its stored caller, runtime, scope, deadline, and parent grants. Child calls keep the original scope and monotonic deadline. Repeated owners, more than 16 call levels, and more than 1,024 active grants are rejected. These are draft safety limits, not performance results.

The host selects the peer's effective settings and validates both query schemas. Consumers cannot supply settings, caller identity, or call routes. Pure processing cannot use the service proxy. `HostServiceAccess` and its provider lookup remain SDK components; no daemon catalog or lifecycle service calls them yet. P03 must coordinate lookup, activation, grant creation, and revocation. Peer commands remain open: they need durable job acceptance in P05 and must not become direct calls to `execute()`.

Query and command adapters check registered schemas, scopes, and runtime identity. Query continuation preserves the selected snapshot. Write-class commands require an expected state revision. The future host job service must check that revision against current state and enforce read-only policy before dispatch. The worker cannot prove those host facts.

Command observations are bounded proposals for original input. They require an owned, declared source type and matching scope. The host must allocate raw IDs, check stored causes, resolve content, and commit observations with the durable outcome. Source and translator protocols now exist, but their host storage path is still pending. A stop acknowledgment does not finish a job, and reconciliation does not call execute again.

`ExtensionSources` describes and reads owned sources in the live lane. Plans contain source identities, watch paths, optional deadlines, and typed source state. A read result repeats the selected source and prior checkpoint. The host must commit its observations and next checkpoint together. No host watches or source checkpoints are installed by the SDK itself.

`ExtensionTranslator` runs in the pure lane. It receives exact recorded bytes, source metadata, captured settings, and decoder state. It returns one explicit decision per input and proposed next state. Each translated fact has a package-owned key inside its scope. `translated_event_id()` excludes raw row IDs and processing revisions. The complete proposal retains repeated observations; `translated_candidates()` selects the first candidate body for each logical ID. Host storage and audit integration are still required.

`ExtensionProjector` has two pure methods. `select_records()` keeps record-key rules in the feature package. The host then captures every selected row, including missing keys, at one snapshot. `project()` receives those rows and committed facts. It returns ordered extension feed entries and owned put or delete operations. Writes require the exact captured revision. Canonical input progress and projection commit cursors remain separate. `projected_entry_id()` uses owner, scope, source fact, and local entry key, not runtime or history revisions. The manifest declares `entry_types` separately from canonical `event_types`.

`ExtensionProjectionTransformer.transform()` receives captured core state, canonical facts, record state, and ordered write proposals. It returns explicit keep, replace, drop, or insert operations. Core session and actor display changes must preserve execution state. Use canonical transforms for lifecycle changes. Core feed replacements preserve source references; new feed rows have derived IDs. Peer rows can be changed or suppressed, but retain their owner, schema, and expected record revision. New extension rows must belong to the current package.

Projection validation rejects a whole invalid proposal. It does not write records or feed entries. The host still must read and recheck the snapshot, verify peer declarations, assign entry positions and commit revisions, and commit all core and extension changes with input progress. The core mapper and projection-transform worker exist, but the current session repository still permits one core feed row per event. No application caller uses this new path yet.

`ExtensionMigrations` provides `migrate_settings()` and `migrate_records()` in the pure lane. A manifest declares exact `migration_paths` with kind, source schema, target schema, and collection for records. Both schemas are owned and registered. The target must be the package's current declared schema. Reverse conversion requires its own explicit path; the host does not infer a downgrade or chain intermediate versions.

Migration calls name an inactive candidate, scope, prepared runtime, and call ID. Settings repeat the captured source revision. Record batches repeat their source snapshot and return one replacement for each captured row in the same order. Keys and expected revisions cannot change. A failed result cannot contain partial candidate data. All new values must match the exact target schema. Requests have an 8 MiB encoded limit; replies have a 4 MiB limit; record batches contain at most 1,000 rows.

These are pure conversion contracts, not upgrade transactions. The host still must capture real source state, prevent credentials from entering settings documents, check candidate and source revisions, store complete results, and switch active heads atomically. Record creation, deletion, or key changes use projection or explicit rebuild paths, not schema migration. Old deletion markers remain storage metadata and are not passed as value-conversion rows.

`ExtensionObserver` handles one committed trigger per durable job. `ObserverSelection` declares input types, scopes, read or write effects, and reconciliation support. A job binds owner, scope, runtime, history, trigger, job ID, and call ID. Replay is not a valid observer request mode. Write observers require an expected state revision. The SDK checks types and a finite positive deadline; the host must check actual acceptance, current state, read-only policy, and time limits.

Observer results preserve the complete binding. Each new observation retains the trigger as a cause and uses an owned declared source schema. Requests are limited to 8 MiB and replies to 4 MiB. `cancel_observation()` has reserved control capacity. `reconcile_observation()` inspects evidence instead of calling `observe()` again. A failed or lost result must not be treated as permission to repeat an external write. Host cursor transactions, job deduplication, cause resolution, cycle limits, result storage, and durable recovery remain required.

The SDK does not provide application discovery, activation transactions, or durable command recovery. The host now has a managed process adapter with private dependency environments, output bounds, failure monitoring, call-grant revocation, and owned shutdown. Canceling a wait does not stop a running Python thread or reverse a side effect. The host must still connect this adapter to lifecycle state and reconcile uncertain jobs. The current daemon does not start these workers.

Terminal presenters receive a fixed data document, settings, scope, snapshot cursor, theme, viewport, and selection state. They run in the pure lane and return display data only. The current Kitty client does not render these new blocks yet. Actions are declared command references; the prototype does not execute them.

The separate TypeScript SDK provides generated wire types and an independent view loader. The remaining host-service protocols, Kitty integration, settings page, and complete extension E2E runner are not implemented. See `docs/extensions/phases/01-protocols.md` in the host repository for the next tasks.
