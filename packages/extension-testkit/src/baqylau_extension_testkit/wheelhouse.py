# Copyright (c) 2026 Zhambyl Yermagambet
"""Give a package under test its offline wheelhouse and hash-locked requirements, with no network.

The built SDK wheel is an explicit input (`BAQYLAU_SDK_WHEEL`). The runtime
dependencies of the SDK, and the package's own runtime dependencies from its
`pyproject.toml`, are packed from this Python environment with the standard
`wheel` tool, so the host's offline install finds every wheel that the lock
names.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess  # noqa: S404 -- The standard wheel tool packs installed distributions.
import sys
import tempfile
from importlib import metadata
from pathlib import Path

from packaging.utils import parse_wheel_filename

from baqylau_extension_testkit import dependencies

SDK_WHEEL_VARIABLE = "BAQYLAU_SDK_WHEEL"
SKIPPED_FILES = frozenset(("RECORD", "INSTALLER", "REQUESTED", "direct_url.json"))
PACK_SECONDS = 120


def build_environment(package: Path, sdk_wheel: Path | None = None) -> None:
    """Write `wheels/` and `requirements.lock` into the package.

    Raises:
        FileNotFoundError: If no built SDK wheel is given.

    """
    selected = sdk_wheel or Path(os.environ.get(SDK_WHEEL_VARIABLE, ""))
    if not selected.is_file():
        message = f"set {SDK_WHEEL_VARIABLE} to the built {dependencies.SDK} wheel"
        raise FileNotFoundError(message)
    wheels = package / "wheels"
    wheels.mkdir(exist_ok=True)
    shutil.copy2(selected, wheels)
    for name in sorted(dependencies.worker_distributions(package)):
        pack(name, wheels)
    (package / "requirements.lock").write_text(lock_text(wheels), encoding="utf-8")


def pack(name: str, wheels: Path) -> None:
    """Pack one installed distribution into a wheel."""
    distribution = metadata.distribution(name)
    with tempfile.TemporaryDirectory(prefix="baqylau-pack-") as staging:
        for relative in distribution.files or ():
            _copy(distribution, Path(staging), relative)
        subprocess.run(  # noqa: S603 -- The current Python runs the standard wheel tool.
            (sys.executable, "-m", "wheel", "pack", staging, "--dest-dir", str(wheels)),
            check=True, capture_output=True, timeout=PACK_SECONDS,
        )


def lock_text(wheels: Path) -> str:
    """Name each wheel's exact version and SHA-256 hash.

    Returns:
        The lock.

    """
    locked = [_locked_line(path) for path in sorted(wheels.glob("*.whl"))]
    return "".join(f"{line}\n" for line in locked)


def _copy(distribution: metadata.Distribution, staging: Path, relative: metadata.PackagePath) -> None:
    inside = not relative.is_absolute() and ".." not in relative.parts
    if not inside or relative.suffix == ".pyc" or relative.name in SKIPPED_FILES:
        return
    target = staging / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(str(distribution.locate_file(relative)), target)


def _locked_line(path: Path) -> str:
    name, version, _, _ = parse_wheel_filename(path.name)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"{name}=={version} --hash=sha256:{digest}"
