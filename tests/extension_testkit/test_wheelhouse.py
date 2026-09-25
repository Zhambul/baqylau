# Copyright (c) 2026 Zhambyl Yermagambet
"""The kit's offline wheelhouse has the package's own runtime dependencies, not only the SDK's."""

from pathlib import Path

from baqylau_extension_testkit.wheelhouse import build_environment

from tests.extension_host import wheel_fixture

PYPROJECT = """[project]
name = "example"
version = "0.1.0"
dependencies = ["baqylau-extension-api==0.1.0a1", "bashlex"]
"""


def sdk_wheel(directory: Path) -> Path:
    """Build the SDK wheel into a new directory.

    Returns:
        The wheel.

    """
    build = directory / "build"
    build.mkdir()
    return next(wheel_fixture.build_wheels(build).glob("baqylau_extension_api-*.whl"))


def test_the_lock_names_the_package_dependencies(tmp_path: Path) -> None:
    """A dependency in pyproject.toml, and the built SDK wheel, are in the lock and the wheelhouse."""
    wheel = sdk_wheel(tmp_path)
    package = tmp_path / "package"
    package.mkdir()
    (package / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")

    build_environment(package, wheel)

    lock = (package / "requirements.lock").read_text(encoding="utf-8")
    locked = {line.split("==")[0] for line in lock.splitlines()}
    assert {"bashlex", "baqylau-extension-api", "pydantic"} <= locked
    assert (package / "wheels" / wheel.name).is_file()
