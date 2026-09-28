# Copyright (c) 2026 Zhambyl Yermagambet
"""The released wheels contain only their own package, their typing marker, and their metadata (P02-T01)."""

import zipfile
from pathlib import Path

import pytest

from tests.extension_host.wheel_fixture import SDK_ROOT, build_wheels

PACKAGES = Path(__file__).resolve().parents[2] / "packages"
# Host modules must never reach an extension's environment.
HOST_PACKAGES = frozenset((
    "api", "app", "core", "domain", "engine", "extensions", "harness", "repository", "sdk", "tests",
))


@pytest.mark.parametrize(("root", "package"), [
    (SDK_ROOT, "baqylau_extension_api"),
    (PACKAGES / "extension-testkit", "baqylau_extension_testkit"),
    (PACKAGES / "dev-tools", "baqylau_dev"),
])
def test_wheel_contains_only_its_package(tmp_path: Path, root: Path, package: str) -> None:
    """Every file is in the package or its metadata; the typing marker is present; no host module is packed."""
    wheel = next(build_wheels(tmp_path, root).glob(f"{package}-*.whl"))

    names = zipfile.ZipFile(wheel).namelist()

    tops = {name.split("/")[0] for name in names}
    metadata = {top for top in tops if top.endswith(".dist-info")}
    assert tops == {package, *metadata}
    assert len(metadata) == 1
    assert f"{package}/py.typed" in names
    assert not tops & HOST_PACKAGES


def test_policy_wheel_ships_the_template(tmp_path: Path) -> None:
    """The package template is data in the policy wheel, so `baqylau-dev new` works from an installed wheel."""
    wheel = next(build_wheels(tmp_path, PACKAGES / "dev-tools").glob("baqylau_dev-*.whl"))

    names = frozenset(zipfile.ZipFile(wheel).namelist())

    assert {
        "baqylau_dev/template/pyproject.toml.in", "baqylau_dev/template/src/backend.py.in",
        "baqylau_dev/template/tests/e2e/test_package.py.in", "baqylau_dev/template/web/view.js.in",
    } <= names
