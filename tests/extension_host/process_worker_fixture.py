# Copyright (c) 2026 Zhambyl Yermagambet
"""Write a real backend that runs one declared program through the host at activation."""

from pathlib import Path

from baqylau_extension_api.manifest.processes import ProcessDeclaration
from baqylau_extension_api.models.processes import ProcessExited, ProcessFailed, ProcessResult
from pydantic import TypeAdapter

from tests.extension_api import service_samples
from tests.extension_host import manager_process_fixture, package_fixture

OWNER = service_samples.ALPHA
MARKER = "activated-process.json"
PROCESS_RESULT: TypeAdapter[ProcessResult] = TypeAdapter(ProcessResult)


def write_worker(directory: Path, wheels: Path) -> None:
    """Declare `echo` and record the host's result when the worker activates."""
    source = manager_process_fixture.marker_peer(directory, wheels)
    manifest = package_fixture.read_manifest(source)
    contributions = manifest.contributions.model_copy(update={"processes": (
        ProcessDeclaration(name="echo", executable="echo"),
    )})
    package_fixture.save_manifest(source, manifest.model_copy(update={"contributions": contributions}))
    code = (source / manager_process_fixture.BACKEND).read_text(encoding="utf-8")
    code = code.replace(
        "return lifecycle_models.ActivationReady",
        "from baqylau_extension_api.models.processes import ProcessRequest\n"
        "        assert self.host.processes is not None\n"
        "        result = self.host.processes.run_process(ProcessRequest(\n"
        f"            name='echo', arguments=('from', 'worker'), cwd={str(directory)!r},\n"
        "        ))\n"
        f"        Path({str(directory / MARKER)!r}).write_text(result.model_dump_json(), encoding='utf-8')\n"
        "        return lifecycle_models.ActivationReady",
        1,
    )
    package_fixture.write_file(source, manager_process_fixture.BACKEND, code.encode())


def result(directory: Path) -> ProcessExited | ProcessFailed:
    """Read what the worker received from the host.

    Returns:
        The process result that the activation wrote.

    """
    return PROCESS_RESULT.validate_json((directory / MARKER).read_bytes())
