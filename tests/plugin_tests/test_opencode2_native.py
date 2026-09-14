# Copyright (c) 2026 Zhambyl Yermagambet
"""Run the native plugin's event capture checks."""

import shutil
import subprocess  # noqa: S404 -- Run Node's built-in test runner.
from pathlib import Path


def test_slow_delivery_does_not_block_capture() -> None:
    """Save all native records while HTTP replies are still pending."""
    path = Path(__file__).with_name("opencode2_native.test.mjs")
    subprocess.run(  # noqa: S603 -- Run a fixed test file from this repository.
        (shutil.which("node") or "node", "--test", str(path)),
        check=True,
        capture_output=True,
        timeout=10,
    )
