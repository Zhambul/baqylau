# Release record: extension API 0.1.0a1

Status: not ready. The artifacts build, and the repeatable gates pass, but the
real Kitty E2E and the live harness checks have not run on this machine. Do not
publish until they pass. Publication follows the user's release workflow; this
record does not publish anything.

## Artifacts

`make release-artifacts` builds these files into `dist/release/` (2026-09-25):

| File | SHA-256 |
| --- | --- |
| `baqylau_dev-0.1.0a1-py3-none-any.whl` | `c709c3dca10fce0ee3bcac5a436ffd9d72b2778b43d8575e4d90e0c1d0c07bfb` |
| `baqylau_extension_api-0.1.0a1-py3-none-any.whl` | `6aaa6b4d50d7cb94fe4605a2fd180b757a2e5843240b1a6f6ae8a318dbc06b82` |
| `baqylau_extension_testkit-0.1.0a1-py3-none-any.whl` | `c295d6d072b805c2ac1dee5c216115e4ddd81b316e238e4c99d4b74291e01c42` |
| `baqylau-dev-tools-0.1.0-alpha.1.tgz` | `f410cb036c0b808ec63ba14b5ea4b2fcc9d70260ebb0a4bd4611719a50514a30` |
| `baqylau-extension-api-0.1.0-alpha.1.tgz` | `5853497bd96dbaa6ecfaedfb6c5ad633e54a469784155ad5ec8f9b8ff15cc908` |
| `policy-report.txt` | `6e5dff24173fd5671773aa6a3f37b38dd804600de8ea5a9553e26c5cad0773db` |

## Compatibility

- License: proprietary, all rights reserved (`LICENSE`; `LicenseRef-Proprietary` in the wheels, `UNLICENSED` in the npm packages).

- Python SDK `baqylau-extension-api` 0.1.0a1 and web SDK `@baqylau/extension-api` 0.1.0-alpha.1 are one API version. A package pins it exactly: `api_requires: "==0.1.0a1"`.
- `baqylau-extension-testkit` 0.1.0a1 depends on `baqylau-extension-api==0.1.0a1`. It has its own reply models for host routes, and `tests/extension_testkit/test_testkit_models.py` checks every field that it reads against the host's published OpenAPI.
- Shared policy `baqylau-dev` 0.1.0a1 and `@baqylau/dev-tools` 0.1.0-alpha.1; policy SHA-256 `08592205a2dc1402670078660645ab88073a2786d0877d3e33a2916df4f55d8f` (`dist/release/policy-report.txt` lists each tool pin). A package declares the policy in `quality_policy`.
- Changes in this version that an author must know: a canonical transformer declares `prior_state` to receive earlier facts, and pure calls have a 5-second deadline (see `authoring.md`).
- More changes that an author must know (see `authoring.md`): a terminal view can name a `query`, and the host reads it with the pane's focus (`TerminalViewInput`) and gives the result to the presenter as its document. The web view `api` has `query`, `watchChanges`, `runCommand`, and `readJob`. A repository scope ID comes from the SDK rule in `baqylau_extension_api.repositories`. A `workspace_page` view with the `repository` scope opens at `#/repo/<directory>/x/<extension>/<view>`. `python -m baqylau_dev new` writes a new package.
- A feed `replace` view can target an entry type of its own package. Its view gets the entry's JSON document in `subject.document` (web SDK `FeedEntrySubject`). Before, an extension entry was always a note line.
- A package that sets `uses_sessions` gets `host.sessions.repository_sessions(...)`. It lists the sessions whose working directory is in a repository, so a repository view can read session-scoped peer data.
- The test kit's `build_environment` packs the package's own `[project].dependencies` from `pyproject.toml`, as well as the SDK's.
- Browser test pins are separate for each language: Python `playwright==1.62.0` and `pytest-playwright==0.9.0` (`requirements-dev.txt`, and the test kit's `browser` extra allows `>=1.50,<2`), and JS `@playwright/test` `1.62.1` (`@baqylau/dev-tools/pins`). Each one installs its own browser builds.

## Policy release updates

A policy release changes `baqylau-dev` and `@baqylau/dev-tools` together, and gives a new policy SHA-256 in `policy-report.txt`. To move a maintained package to the new release:

1. Set `policy_version` in `baqylau-dev.toml` and `quality_policy` in `extension.json` to the new version.
2. Set the new `@baqylau/dev-tools` version and each tool version that `baqylau-dev-tools-pins` names in `package.json`.
3. Run `make lint` (and `make lint-web` for a web package), and fix the new findings. Do not add local rule changes: the parity gate refuses them.

The parity gate refuses a package whose `policy_version` is not the installed release, and the pin check refuses a tool version that is not the pinned version. Thus a package cannot use the new tools with the old policy by accident. The host moves in the same change: `requirements-dev.txt`, the lockfiles, and `make policy-check`.

## Gates

| Gate | Result |
| --- | --- |
| `make lint` (policy parity, frontend lint and types, mypy, Vulture, Wemake, Ruff) | passes |
| Python suite without Kitty (`pytest -m "not kitty" --ignore=tests/e2e`) | passes; the count is in the P08-T05 record |
| Dashboard and web SDK unit tests (Vitest) | pass |
| Extension browser specs (`extension-views`, `extensions`, `extension-settings`) in Chromium and WebKit | pass |
| Core `dashboard.spec.ts` in a git worktree | 7 cases fail for worktree reasons (branch name in screenshots, fixture directory); not run on the main checkout |
| C26 outside the checkout and the author example through the installed runner | pass |
| `make test-release-consumers`: a clean wheel environment reports the same policy as the host (`diff` of the reports), a new package passes the Python gates and its unit test, the new web package and C26 pass their npm gates with the tarballs | pass locally; the CI `release` job runs it and keeps `dist/release` as a private run artifact; no remote result yet |
| Real Kitty E2E (`pytest -m kitty`) | not run: needs the user's approval, because it opens windows |
| Live harness and adapters checks | not run: need a configured live environment and the user's approval |
