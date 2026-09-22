# Copyright (c) 2026 Zhambyl Yermagambet
"""Check that declared runtime files must be inside the captured package."""

from pathlib import Path

import pytest
from baqylau_extension_api.manifest.metadata import BackendEnvironment

from extensions.discovery_manifest import checked_package_digest
from tests.extension_host import package_fixture


@pytest.mark.parametrize("missing", ["requirements.lock", "wheels/dependency.whl"])
def test_discovery_requires_runtime_files(tmp_path: Path, missing: str) -> None:
    """Environment declarations cannot refer to absent package bytes."""
    source = package_fixture.write_package(tmp_path)
    manifest = package_fixture.read_manifest(source)
    assert manifest.backend is not None
    backend = manifest.backend.model_copy(update={
        "environment": BackendEnvironment(requirements="requirements.lock", wheelhouse="wheels"),
    })
    selected = manifest.model_copy(update={"backend": backend})
    package_fixture.save_manifest(source, selected)
    for path in ("requirements.lock", "wheels/dependency.whl"):
        if path != missing:
            package_fixture.write_file(source, path, b"discovery-only file")
    with pytest.raises(ValueError, match="package-owned"):
        checked_package_digest(source, selected, selected.model_dump_json().encode())


@pytest.mark.parametrize("path", ["/outside/lock", "../lock", "wheels/../../lock"])
def test_environment_paths_cannot_escape(path: str) -> None:
    """The public manifest retains its checked relative-path type."""
    with pytest.raises(ValueError, match=r"relative|traversal"):
        BackendEnvironment(requirements=path, wheelhouse="wheels")
