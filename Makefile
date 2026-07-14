PY ?= python3

# The hermetic e2e suite (fake kitten, per-test tmp dirs). See docs/testing.md.
# Parallel by default (pytest-xdist) — every test is tmpdir-isolated so this is
# safe; use test-seq for debugging or where xdist is unavailable.
test:
	$(PY) -m pytest -q -m "not kitty" -n auto

# Sequential run of the same suite.
test-seq:
	$(PY) -m pytest -q -m "not kitty"

# Everything, including the opt-in real-kitty smoke tests (needs kitty installed).
test-all:
	CLAUDE_E2E_KITTY=1 $(PY) -m pytest -q

# Alias for the (now default-parallel) suite; kept for muscle memory.
test-par: test

.PHONY: test test-seq test-all test-par
