PY ?= .venv/bin/python

# The hermetic e2e suite (fake kitten, per-test tmp dirs). See docs/testing.md.
# Parallel by default (pytest-xdist) — every test is tmpdir-isolated so this is
# safe; use test-seq for debugging or where xdist is unavailable.
test:
	$(PY) -m pytest -q -m "not kitty" -n auto --ignore=tests/e2e

# Sequential run of the same suite.
test-seq:
	$(PY) -m pytest -q -m "not kitty" --ignore=tests/e2e

# Everything, including the opt-in real-kitty smoke tests (needs kitty installed).
test-all:
	CLAUDE_E2E_KITTY=1 $(PY) -m pytest -q --ignore=tests/e2e

# The LIVE-harness suite (tests/e2e): the real daemon on its own port and
# databases, the real CLI in a pseudo-terminal, a real workspace on disk. Catches
# a harness release changing its evidence under us — the failure nothing
# simulated can see. Spends tokens, so it is opt-in and sequential (one daemon,
# one workspace). It stops on the first failed scenario so a broken live
# integration does not keep spending tokens. See tests/e2e/conftest.py.
#
#   make test-drift                                  the Examples tables as written
#   make test-drift E2E="--e2e-model claude-opus-5"   every scenario, one model
#   make test-drift E2E="-k codex --e2e-data-dir /tmp/drift"   keep the databases
test-drift:
	$(PY) -m pytest tests/e2e -q -x $(E2E)

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
lint: typecheck deadcode
	$(PY) -m ruff check .

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
TYPECHECK_PATHS = api app bin client core dashboard audit domain engine harness notify repository terminal tests

typecheck:
	$(PY) -m mypy $(TYPECHECK_PATHS)

lint-fix:
	$(PY) -m ruff check . --fix

# Dead code (vulture). Ruff's F rules see one file at a time — an unused import,
# an unused local. Nothing there can tell you a function is called by NOBODY, so
# this pass reads the whole tree at once and reports what is defined and never
# referenced.
#
# The paths are the product packages, deliberately WITHOUT tests/: a helper that
# only its own test calls is unreferenced product code, and naming tests/ here
# would hide exactly that. (To see which findings tests do reach, add tests to
# the path list and diff the two runs.)
#
# The two .py files are vulture whitelists, not sources — see their headers.
DEADCODE_PATHS = api app bin client core dashboard audit domain engine harness notify repository terminal
DEADCODE_WHITELISTS = vulture-allowlist.py vulture-baseline.py
# Call sites vulture cannot see: the framework invokes these, never our code.
# Matched by SHAPE, not by router name — `router`, `web` and `guarded` are three
# APIRouters today, and a fourth must not silently read as dead code.
DEADCODE_DECORATORS = @*.get,@*.post,@*.put,@*.patch,@*.delete,@*.websocket,@model_validator,@field_validator

deadcode:
	$(PY) -m vulture $(DEADCODE_PATHS) $(DEADCODE_WHITELISTS) \
		--ignore-decorators "$(DEADCODE_DECORATORS)"

# The same scan with the baseline OFF — the standing backlog of dead code.
# Not a gate; run it when you want something to delete.
deadcode-backlog:
	@$(PY) -m vulture $(DEADCODE_PATHS) vulture-allowlist.py \
		--ignore-decorators "$(DEADCODE_DECORATORS)" || true

.PHONY: test test-seq test-all test-drift test-par lint lint-fix typecheck deadcode deadcode-backlog
