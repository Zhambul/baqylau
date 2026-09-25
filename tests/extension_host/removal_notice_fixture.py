# Copyright (c) 2026 Zhambyl Yermagambet
"""Write a provider and an optional consumer that records each activation request."""

from pathlib import Path

from baqylau_extension_api.models.lifecycle import ActivationRequest

from tests.extension_api import service_samples
from tests.extension_host import manager_process_fixture, package_fixture, runtime_host_fixture

CONSUMER = service_samples.ALPHA
PROVIDER = service_samples.BETA
MARKER = "consumer-activation.json"


def write_peers(directory: Path, wheels: Path) -> None:
    """Write the provider and the consumer; the consumer writes its activation request to a marker."""
    consumer, _provider = runtime_host_fixture.write_peers(directory, wheels, (CONSUMER, PROVIDER))
    code = (consumer / manager_process_fixture.BACKEND).read_text(encoding="utf-8")
    imports = "from dataclasses import dataclass"
    code = code.replace(imports, f"{imports}\nfrom pathlib import Path", 1)
    code = code.replace(
        "return lifecycle_models.ActivationReady",
        f"Path({str(directory / MARKER)!r}).write_text(request.model_dump_json(), encoding='utf-8')\n"
        "        return lifecycle_models.ActivationReady",
        1,
    )
    package_fixture.write_file(consumer, manager_process_fixture.BACKEND, code.encode())


def last_activation(directory: Path) -> ActivationRequest:
    """Read the consumer's last activation request.

    Returns:
        The request that the consumer worker received.

    """
    return ActivationRequest.model_validate_json((directory / MARKER).read_bytes())
