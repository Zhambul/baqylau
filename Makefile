PY ?= python3

# The hermetic e2e suite (fake kitten, per-test tmp dirs). See docs/testing.md.
test:
	$(PY) -m pytest -q -m "not kitty"

# Everything, including the opt-in real-kitty smoke tests (needs kitty installed).
test-all:
	CLAUDE_E2E_KITTY=1 $(PY) -m pytest -q

# Parallel run (pytest-xdist); every test is tmpdir-isolated so this is safe.
test-par:
	$(PY) -m pytest -q -m "not kitty" -n auto

.PHONY: test test-all test-par
