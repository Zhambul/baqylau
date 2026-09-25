# Copyright (c) 2026 Zhambyl Yermagambet
"""Pack installed test dependencies with the standard wheel tool, without a network."""

import os
import shutil
import sys
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from extensions.models.processes import PreparationCommand
from extensions.preparation_runner import BoundedPreparationRunner

SDK_ROOT = Path(__file__).resolve().parents[2] / "packages" / "extension-api"


def build_wheels(directory: Path, package_root: Path = SDK_ROOT) -> Path:
    """Build one real package, the SDK by default, and pack its installed mandatory dependencies for tests.

    Returns:
        Offline wheel files with regenerated RECORD data, not a release wheelhouse.

    """
    wheelhouse = directory / "wheels"
    wheelhouse.mkdir()
    source = directory / "source"
    shutil.copytree(package_root, source, ignore=shutil.ignore_patterns("build", "*.egg-info", "__pycache__"))
    _run(directory, (
        "pip", "wheel", str(source), "--no-build-isolation", "--no-deps", "--no-index",
        "--no-cache-dir", "--wheel-dir", str(wheelhouse),
    ))
    distributions = metadata.distributions(path=[str(source / "src")])
    for name in dependency_names(next(iter(distributions))):
        _pack_distribution(directory, wheelhouse, name)
    return wheelhouse


def dependency_names(distribution: metadata.Distribution) -> set[str]:
    """Follow the installed SDK's mandatory declared dependency graph.

    Returns:
        All direct and transitive package names except the root SDK.

    """
    selected: set[str] = set()
    pending = list(_requirements(distribution))
    while pending:
        name = pending.pop()
        if name not in selected:
            selected.add(name)
            pending.extend(_requirements(metadata.distribution(name)))
    return selected


def _requirements(distribution: metadata.Distribution) -> tuple[str, ...]:
    declarations = distribution.requires or ()
    parsed = tuple(Requirement(line) for line in declarations)
    assert not any(requirement.extras for requirement in parsed)
    return tuple(
        canonicalize_name(requirement.name)
        for requirement in parsed
        if _is_required(requirement)
    )


def _is_required(requirement: Requirement) -> bool:
    return requirement.marker is None or requirement.marker.evaluate({"extra": ""})


def _pack_distribution(directory: Path, wheelhouse: Path, name: str) -> None:
    distribution = metadata.distribution(name)
    staging = directory / name
    staging.mkdir()
    assert distribution.files is not None
    for relative in distribution.files:
        _copy_distribution_file(distribution, staging, relative)
    _run(directory, ("wheel", "pack", str(staging), "--dest-dir", str(wheelhouse)))


def _copy_distribution_file(
    distribution: metadata.Distribution, staging: Path, relative: metadata.PackagePath,
) -> None:
    if relative.is_absolute() or ".." in relative.parts or relative.suffix == ".pyc":
        return
    if relative.name in {"RECORD", "INSTALLER", "REQUESTED", "direct_url.json"}:
        return
    target = staging / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(str(distribution.locate_file(relative)), target)


def _run(directory: Path, arguments: tuple[str, ...]) -> None:
    command = PreparationCommand(
        (sys.executable, "-I", "-m", *arguments), {"PATH": os.defpath}, directory,
    )
    output = BoundedPreparationRunner().run_preparation(command)
    assert output.return_code == 0, output.stderr.decode()
