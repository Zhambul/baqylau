# Copyright (c) 2026 Zhambyl Yermagambet
"""Write a real backend that reads one declared secret through the host at activation."""

from pathlib import Path

from baqylau_extension_api.manifest.settings import SecretSetting, SettingsDefinition
from baqylau_extension_api.models.credentials import SecretAvailable, SecretMissing, SecretResult
from baqylau_extension_api.models.documents import EncodedDocument
from pydantic import TypeAdapter

from tests.extension_api import service_samples
from tests.extension_host import manager_process_fixture, package_fixture

OWNER = service_samples.ALPHA
NAME = "token"
MARKER = "activated-secret.json"
SECRET_RESULT: TypeAdapter[SecretResult] = TypeAdapter(SecretResult)


def write_worker(directory: Path, wheels: Path) -> None:
    """Declare one required secret and record what the worker reads when it activates."""
    source = manager_process_fixture.marker_peer(directory, wheels)
    manifest = package_fixture.read_manifest(source)
    package_fixture.save_manifest(source, manifest.model_copy(update={"settings": SettingsDefinition(
        defaults=EncodedDocument(
            schema_ref=service_samples.schema(service_samples.ALPHA).reference, json_text='"ordinary"',
        ),
        scopes=("installation",),
        secret_references=(SecretSetting(name=NAME, required=True),),
    )}))
    code = (source / manager_process_fixture.BACKEND).read_text(encoding="utf-8")
    code = code.replace(
        "return lifecycle_models.ActivationReady",
        "from baqylau_extension_api.models.credentials import SecretRequest\n"
        "        assert self.host.credentials is not None\n"
        f"        resolved = self.host.credentials.resolve_secret(SecretRequest(name={NAME!r}))\n"
        f"        Path({str(directory / MARKER)!r}).write_text(resolved.model_dump_json(), encoding='utf-8')\n"
        "        return lifecycle_models.ActivationReady",
        1,
    )
    package_fixture.write_file(source, manager_process_fixture.BACKEND, code.encode())


def resolved(directory: Path) -> SecretAvailable | SecretMissing:
    """Read what the worker received from the host.

    Returns:
        The secret result that the activation wrote.

    """
    return SECRET_RESULT.validate_json((directory / MARKER).read_bytes())
