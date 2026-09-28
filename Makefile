PY ?= .venv/bin/python
.DEFAULT_GOAL := frontend-install
NPM ?= npm
E2E_WORKERS ?= 6
BROWSER_E2E_WORKERS ?= 4
E2E_DIST ?= load
FRONTEND_DIR = dashboard/frontend
FRONTEND_MODULES = $(FRONTEND_DIR)/node_modules/.package-lock.json
FRONTEND_POLICY = $(wildcard packages/dev-tools-web/*.mjs packages/dev-tools-web/*.json)
DEV_TOOLS_WEB_DIR = packages/dev-tools-web
DEV_TOOLS_WEB_MODULES = $(DEV_TOOLS_WEB_DIR)/node_modules/.package-lock.json
DEV_TOOLS_WEB_BUILD = $(DEV_TOOLS_WEB_DIR)/dist/coverage.d.mts
DEV_TOOLS_CHECKS_DIR = $(DEV_TOOLS_WEB_DIR)/test
DEV_TOOLS_CHECKS_MODULES = $(DEV_TOOLS_CHECKS_DIR)/node_modules/.package-lock.json
EXTENSION_WEB_DIR = packages/extension-api-web
EXTENSION_WEB_MODULES = $(EXTENSION_WEB_DIR)/node_modules/.package-lock.json
EXTENSION_WEB_BUILD = $(EXTENSION_WEB_DIR)/dist/index.js
EXTENSION_WEB_SOURCES = $(wildcard $(EXTENSION_WEB_DIR)/src/*.ts $(EXTENSION_WEB_DIR)/src/*/*.ts)

$(DEV_TOOLS_WEB_MODULES): $(DEV_TOOLS_WEB_DIR)/package.json $(DEV_TOOLS_WEB_DIR)/package-lock.json
	cd $(DEV_TOOLS_WEB_DIR) && $(NPM) ci

$(DEV_TOOLS_WEB_BUILD): $(DEV_TOOLS_WEB_MODULES) $(FRONTEND_POLICY)
	cd $(DEV_TOOLS_WEB_DIR) && $(NPM) run build

$(DEV_TOOLS_CHECKS_MODULES): $(DEV_TOOLS_CHECKS_DIR)/package.json $(DEV_TOOLS_CHECKS_DIR)/package-lock.json $(DEV_TOOLS_CHECKS_DIR)/.npmrc $(DEV_TOOLS_WEB_BUILD)
	cd $(DEV_TOOLS_CHECKS_DIR) && $(NPM) ci

$(EXTENSION_WEB_MODULES): $(EXTENSION_WEB_DIR)/package.json $(EXTENSION_WEB_DIR)/package-lock.json $(EXTENSION_WEB_DIR)/.npmrc $(DEV_TOOLS_WEB_BUILD)
	cd $(EXTENSION_WEB_DIR) && $(NPM) ci

# The dashboard imports the public web SDK's view host from its built output.
$(EXTENSION_WEB_BUILD): $(EXTENSION_WEB_MODULES) $(EXTENSION_WEB_SOURCES)
	cd $(EXTENSION_WEB_DIR) && $(NPM) run build

$(FRONTEND_MODULES): $(FRONTEND_DIR)/package.json $(FRONTEND_DIR)/package-lock.json $(FRONTEND_DIR)/.npmrc $(DEV_TOOLS_WEB_BUILD) $(EXTENSION_WEB_BUILD)
	cd $(FRONTEND_DIR) && $(NPM) ci

frontend-install: $(FRONTEND_MODULES)

extension-web-install: $(EXTENSION_WEB_MODULES)

build-frontend: frontend-install
	cd $(FRONTEND_DIR) && $(NPM) run build
	$(PY) -m dashboard.frontend_build --stamp

test-frontend: frontend-install
	cd $(FRONTEND_DIR) && $(NPM) run format:check
	cd $(FRONTEND_DIR) && $(NPM) run check
	cd $(FRONTEND_DIR) && $(NPM) run test:coverage
	$(MAKE) --no-print-directory test-extension-web
	$(MAKE) --no-print-directory test-dev-tools-web

# The shared frontend rules must fail on deliberate violations, and the dashboard must keep them.
test-dev-tools-web: $(DEV_TOOLS_CHECKS_MODULES) frontend-install
	cd $(DEV_TOOLS_CHECKS_DIR) && $(NPM) test

test-extension-web: extension-web-install
	cd $(DEV_TOOLS_WEB_DIR) && $(NPM) run format:check
	cd $(EXTENSION_WEB_DIR) && BAQYLAU_SDK_PYTHON=$(abspath $(PY)) $(NPM) run generate:check
	cd $(EXTENSION_WEB_DIR) && $(NPM) run format:check
	cd $(EXTENSION_WEB_DIR) && $(NPM) run check
	cd $(EXTENSION_WEB_DIR) && $(NPM) run test:coverage

test-browser: browser-static-e2e

browser-static-e2e:
	cd $(FRONTEND_DIR) && BAQYLAU_E2E_PYTHON=$(abspath $(PY)) BAQYLAU_E2E_WORKERS=$(BROWSER_E2E_WORKERS) $(NPM) run test:browser

lint-frontend: frontend-install extension-web-install
	cd $(FRONTEND_DIR) && $(NPM) run lint
	cd $(DEV_TOOLS_WEB_DIR) && $(NPM) run check
	cd $(EXTENSION_WEB_DIR) && $(NPM) run check
	cd $(EXTENSION_WEB_DIR) && $(NPM) run build
	cd $(EXTENSION_WEB_DIR) && $(NPM) run lint

# The hermetic e2e suite (fake kitten, per-test tmp dirs). See docs/testing.md.
# Parallel by default (pytest-xdist) — every test is tmpdir-isolated so this is
# safe; use test-seq for debugging or where xdist is unavailable.
test-python: build-frontend
	$(PY) -m pytest -q -m "not kitty" -n auto --ignore=tests/e2e

# Replay known audit failures without a live model or user data.
test-audit-replay: build-frontend
	$(PY) -m pytest tests/e2e_replay -q

test: test-frontend test-browser test-python

# Sequential run of the same suite.
test-seq:
	$(PY) -m pytest -q -m "not kitty" --ignore=tests/e2e

# The public SDK tests use the installed package and no live harness.
test-extension-api:
	$(PY) -m pytest tests/extension_api -q

# Everything, including the opt-in real-kitty smoke tests (needs kitty installed).
test-all:
	CLAUDE_E2E_KITTY=1 $(PY) -m pytest -q --ignore=tests/e2e

# The LIVE-harness suite (tests/e2e): the real daemon on its own port and
# databases, the real CLI in a pseudo-terminal, a real workspace on disk. Catches
# a harness release changing its evidence under us — the failure nothing
# simulated can see. Spends tokens, so it is opt-in. Each xdist worker owns its
# daemon, databases, Codex home, and workspace copy. It stops after the first
# failed scenario so a broken live integration does not keep spending tokens.
# See tests/e2e/conftest.py.
#
#   make test-drift                                  the Examples tables as written
#   make test-drift E2E="--e2e-model claude-opus-5"   every scenario, one model
#   make test-drift E2E="-k codex --e2e-data-dir /tmp/drift"   keep the databases
test-drift:
	$(PY) -m pytest tests/e2e/test_scenarios.py tests/e2e/test_chrome_permission.py -q -x -n $(E2E_WORKERS) --dist $(E2E_DIST) --maxschedchunk 1 $(E2E)

test-browser-drift: build-frontend browser-live-e2e

browser-live-e2e:
	BAQYLAU_E2E_BROWSER=1 $(PY) -m pytest tests/e2e/browser -q -x -n $(E2E_WORKERS) --dist $(E2E_DIST) --maxschedchunk 1 $(E2E)

terminal-live-e2e:
	kitten @ ls > /dev/null
	BAQYLAU_E2E_REAL_TERMINAL=1 $(PY) -m pytest tests/e2e/real_terminal -q -x -n 0 $(E2E)

# Complete end-to-end gate. Every suite uses its measured parallelism. Suite
# boundaries stay serial, so one failure stops before the next token-spending
# layer starts. Playwright rebuilds the frontend before its suite.
e2e: build-frontend
	$(MAKE) --no-print-directory test-audit-replay
	$(MAKE) --no-print-directory test-drift
	$(MAKE) --no-print-directory terminal-live-e2e
	$(MAKE) --no-print-directory browser-live-e2e
	$(MAKE) --no-print-directory browser-static-e2e

# Alias for the (now default-parallel) suite; kept for muscle memory.
test-par: test

# Lint (ruff — config in ruff.toml encodes docs/styleguide.md; CI-enforced)
# plus the cross-module dead-code scan below and the type gate. Three gates,
# one command.
# Typecheck FIRST, deliberately. Make runs prerequisites in order, and with
# `deadcode` first a failing dead-code scan meant mypy never ran at all — the
# type gate went quiet instead of red, and stayed quiet for a whole refactor
# while 523 errors accumulated behind it. The cheapest gate is not the most
# important one.
lint: policy-check lint-frontend typecheck deadcode wemake
	$(PY) -m baqylau_dev check --gate ruff

policy-check:
	$(PY) -m baqylau_dev check --gate parity
	$(PY) -m baqylau_dev report

policy-generate:
	$(PY) -m baqylau_dev generate

# Build the shared packages for a release; publication is a separate, manual step.
RELEASE_DIR = dist/release
release-artifacts:
	rm -rf $(RELEASE_DIR) && mkdir -p $(RELEASE_DIR)
	for package in extension-api extension-testkit dev-tools; do \
		$(PY) -m pip wheel --no-deps --no-build-isolation --wheel-dir $(RELEASE_DIR) packages/$$package || exit 1; \
		rm -rf packages/$$package/build; \
	done
	cd $(EXTENSION_WEB_DIR) && npm run build && npm pack --pack-destination ../../$(RELEASE_DIR)
	cd packages/dev-tools-web && npm pack --pack-destination ../../$(RELEASE_DIR)
	$(PY) -m baqylau_dev report > $(RELEASE_DIR)/policy-report.txt
	cd $(RELEASE_DIR) && shasum -a 256 *.whl *.tgz policy-report.txt > SHA256SUMS

# A new package's web part passes its npm gates with the built artifacts. Run release-artifacts first.
# Each consumer runs in a new directory outside the checkout, which is removed at the end.
RELEASE_TARBALLS = $(abspath $(RELEASE_DIR))
test-template-web:
	package=$$(mktemp -d)/package && trap 'rm -rf "$$(dirname $$package)"' EXIT && \
	$(PY) -m baqylau_dev new --root $$package --id example.fresh --web && cd $$package && \
	$(NPM) pkg set \
		devDependencies.@baqylau/dev-tools=file:$(RELEASE_TARBALLS)/baqylau-dev-tools-0.1.0-alpha.1.tgz \
		devDependencies.@baqylau/extension-api=file:$(RELEASE_TARBALLS)/baqylau-extension-api-0.1.0-alpha.1.tgz && \
	$(NPM) install --ignore-scripts && $(MAKE) --no-print-directory lint-web

# A clean environment with only the wheels must report the same policy as the host.
test-release-consumers: test-template-web
	consumer=$$(mktemp -d) && trap 'rm -rf "$$consumer"' EXIT && \
	$(PY) -m venv $$consumer/venv && $$consumer/venv/bin/python -m pip install --quiet $(RELEASE_TARBALLS)/*.whl && \
	$$consumer/venv/bin/python -m baqylau_dev new --root $$consumer/package --id example.fresh --terminal && \
	cd $$consumer/package && $$consumer/venv/bin/python -m baqylau_dev report > ../policy-report.txt && \
	diff $(RELEASE_TARBALLS)/policy-report.txt ../policy-report.txt && \
	$$consumer/venv/bin/python -m baqylau_dev check --gate lint && \
	$$consumer/venv/bin/python -m pytest -q --ignore=tests/e2e
	cd $(EXTENSION_WEB_DIR) && $(NPM) run test:external -- ../../$(RELEASE_DIR)

# WPS checks design rules that Ruff does not implement. setup.cfg records the
# project rules that take precedence over conflicting WPS rules.
wemake:
	$(PY) -m baqylau_dev check --gate wemake

# Static types (mypy — config in mypy.ini; CI-enforced). The tree is strict:
# an unannotated function is an error, and mypy.ini's per-package ratchet is
# the only thing holding that back for packages whose migration has not landed.
#
# Ruff's ANN rules in the same gate answer "is there an annotation"; this
# answers "is it TRUE". Both are needed — an annotation nothing checks is a
# comment.
# `client` is in the list: those files are stdlib-only scripts, but they are the
# programs every harness and the terminal actually run, so they get the same gate
# as everything else.
typecheck:
	$(PY) -m baqylau_dev check --gate types

lint-fix:
	$(PY) -m baqylau_dev check --gate parity
	$(PY) -m ruff check --config ruff.toml . --fix

# Dead code (vulture). Ruff's F rules see one file at a time — an unused import,
# an unused local. Nothing there can tell you a function is called by NOBODY, so
# this pass reads the whole tree at once and reports what is defined and never
# referenced.
#
# The paths are the product packages, deliberately WITHOUT tests/: a helper that
# only its own test calls is unreferenced product code, and naming tests/ here
# would hide exactly that. (To see which findings tests do reach, add tests to
# the path list and diff the two runs.)
# `sdk/` is also absent: it is a dev-only test client, and all of its public
# callers are in tests/. It still has strict type, Ruff, architecture, and
# focused behavior gates.
#
# The allowlist is a vulture contract file, not a product source.
deadcode:
	$(PY) -m baqylau_dev check --gate deadcode

.PHONY: release-artifacts test-template-web test-release-consumers frontend-install extension-web-install build-frontend test-frontend test-extension-web test-dev-tools-web test-browser browser-static-e2e test-python test test-seq test-extension-api test-all e2e test-drift test-browser-drift browser-live-e2e terminal-live-e2e test-par lint lint-fix typecheck wemake deadcode policy-check policy-generate
