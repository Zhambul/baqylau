# Copyright (c) 2026 Zhambyl Yermagambet
"""Select one checked feature source path after the installed SDK has started."""

import sys
from importlib import invalidate_caches, util
from pathlib import Path

from baqylau_extension_api.contracts.plugin import ExtensionPlugin
from baqylau_extension_api.contracts.services import ExtensionHostServices
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.runtime.loading import load_backend
from baqylau_extension_api.runtime.preparation import validate_load
from baqylau_extension_api.runtime.worker_models import WorkerLoadRequest


def load_package_backend(
    request: WorkerLoadRequest, services: ExtensionHostServices, directory: Path,
) -> ExtensionPlugin:
    """Load feature source from one host-selected directory, not the working directory.

    Returns:
        The checked plugin from the package-owned source path.

    Raises:
        ExtensionContractError: If no backend exists or its name shadows an installed module.

    """
    validate_load(request, services)
    backend = request.manifest.backend
    if backend is None:
        message = "a backend-free extension does not need a worker"
        raise ExtensionContractError(message)
    source = backend_source_directory(directory, backend.module)
    root_name = backend.module.partition(".")[0]
    if root_name in sys.modules or util.find_spec(root_name) is not None:
        message = "extension backend must not shadow an installed module"
        raise ExtensionContractError(message)
    sys.path.append(str(source))
    invalidate_caches()
    return load_backend(request, services)


def backend_source_directory(directory: Path, module: str) -> Path:
    """Resolve one flat or src-layout backend without importing its package.

    Returns:
        The one package-owned import root that contains the selected backend.

    Raises:
        ExtensionContractError: If the root, source path, or layout is invalid.

    """
    if not directory.is_absolute() or directory.is_symlink() or not directory.is_dir():
        message = "worker package directory must be an absolute non-linked directory"
        raise ExtensionContractError(message)
    stem = module.replace(".", "/")
    choices = (f"{stem}.py", f"{stem}/__init__.py", f"src/{stem}.py", f"src/{stem}/__init__.py")
    found = tuple(relative for relative in choices if (directory / relative).is_file())
    if len(found) != 1:
        message = "worker backend must resolve to one package-owned Python file"
        raise ExtensionContractError(message)
    _require_owned_source(directory, Path(found[0]))
    return directory / "src" if found[0].startswith("src/") else directory


def _require_owned_source(directory: Path, relative: Path) -> None:
    selected = directory
    for part in relative.parts:
        selected /= part
        if selected.is_symlink():
            message = "worker backend source cannot contain links"
            raise ExtensionContractError(message)
