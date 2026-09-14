# Copyright (c) 2026 Zhambyl Yermagambet
"""Call a native plugin method in a private server."""

import os
import subprocess  # noqa: S404 -- Use the installed native API client.
from pathlib import Path
from urllib.parse import urlencode

from harness.impl.opencode2.launch_config import session_configuration
from harness.impl.opencode2.plugin_info import DEFAULT_MODEL_ID
from harness.runtime import HarnessRuntimeConfig

REQUEST_TIMEOUT_SECONDS = 15


def request(harness_runtime_config: HarnessRuntimeConfig, method: str) -> bytes:
    """Call the native plugin without reading its credential store.

    Returns:
        The native response bytes.

    """
    environment = os.environ.copy()
    environment["OPENCODE_CONFIG_CONTENT"] = session_configuration(
        DEFAULT_MODEL_ID, str(harness_runtime_config.configuration_directory), "8377",
    )
    if harness_runtime_config.settings_file is not None:
        environment["OPENCODE_CONFIG"] = str(harness_runtime_config.settings_file)
    query = urlencode((("location[directory]", str(Path.home())),))
    response = subprocess.run(  # noqa: S603 -- Fixed native API operation; no shell.
        (
            harness_runtime_config.executable, "api", "--standalone", "POST",
            f"/api/rpc/baqylau/{method}?{query}", "--data", '{"input":{}}',
        ),
        env=environment, capture_output=True, check=True, timeout=REQUEST_TIMEOUT_SECONDS,
    )
    return response.stdout
