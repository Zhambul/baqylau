# Copyright (c) 2026 Zhambyl Yermagambet
"""Observe settings inside a real external backend's activation request."""

from pathlib import Path

from baqylau_extension_api.manifest.settings import SettingsDefinition
from baqylau_extension_api.models.documents import EncodedDocument
from baqylau_extension_api.models.lifecycle import ActivationRequest

from tests.extension_api import service_samples
from tests.extension_host import manager_process_fixture, package_fixture

MARKER = "activated-settings.json"
FAILED = "failed-settings-worker"
REFUSE = '"refuse"'


def write_worker(directory: Path, wheels: Path) -> None:
    """Write feature-owned settings and failure checks before package capture."""
    source = manager_process_fixture.marker_peer(directory, wheels)
    manifest = package_fixture.read_manifest(source)
    package_fixture.save_manifest(source, manifest.model_copy(update={"settings": SettingsDefinition(
        defaults=EncodedDocument(
            schema_ref=service_samples.schema(service_samples.ALPHA).reference, json_text='"initial"',
        ),
        scopes=("installation", "workspace"),
    )}))
    code = (source / manager_process_fixture.BACKEND).read_text(encoding="utf-8")
    code = code.replace(
        "return lifecycle_models.ActivationReady",
        "assert request.settings is not None\n"
        f"        if request.settings.json_text == {REFUSE!r}:\n"
        f"            Path({str(directory / FAILED)!r}).write_text(str(os.getpid()), encoding='utf-8')\n"
        "            raise RuntimeError('feature rejected private settings')\n"
        f"        Path({str(directory / MARKER)!r}).write_text(request.model_dump_json(), encoding='utf-8')\n"
        "        return lifecycle_models.ActivationReady",
    )
    package_fixture.write_file(source, manager_process_fixture.BACKEND, code.encode())


def activated(directory: Path) -> ActivationRequest:
    """Read what the feature worker received, not what the host intended to send.

    Returns:
        The actual activation request written by the private worker.

    """
    return ActivationRequest.model_validate_json((directory / MARKER).read_bytes())
