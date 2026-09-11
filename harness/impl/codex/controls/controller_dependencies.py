# Copyright (c) 2026 Zhambyl Yermagambet
"""Own shared Codex control dependencies."""

from harness.impl.codex import definition
from harness.impl.codex.continuity import RewindContinuity

rewind_continuity = RewindContinuity()
DEFAULT_RUNTIME_CONFIG = definition.default_runtime_config()
