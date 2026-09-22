# Copyright (c) 2026 Zhambyl Yermagambet
"""Check strict installed rules outside the host checkout."""

import configparser
import tomllib
from pathlib import Path

import pytest
from baqylau_dev import configuration, parity, profiles
from baqylau_dev.models import ProjectProfile

from tests.dev_tools import package_fixture as fixtures

ENCODING = "utf-8"
MYPY = "mypy"


def test_external_package_passes_python_lint(tmp_path: Path) -> None:
    """Resolve the installed base rules from a package with spaces in its path."""
    root = tmp_path / "independent package"
    root.mkdir()
    fixtures.create_package(root)
    completed = fixtures.invoke(root, "lint")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert (root / ".baqylau-dev" / "ruff.toml").is_file()


def test_external_mypy_has_no_host_exceptions(tmp_path: Path) -> None:
    """Generate global strict settings with no host migration sections."""
    fixtures.create_package(tmp_path)
    profile = profiles.load_profile(tmp_path)
    parser = configparser.ConfigParser()
    parser.read_string(configuration.mypy_configuration(tmp_path, profile))
    assert parser.sections() == [MYPY]
    assert parser.getboolean(MYPY, "strict")
    assert parser.getboolean(MYPY, "warn_unused_ignores")
    assert parser.getboolean(MYPY, "warn_unreachable")


@pytest.mark.parametrize("filename", ["ruff.toml", ".ruff.toml", "mypy.ini", ".mypy.ini", ".flake8", "setup.cfg"])
def test_external_local_override_is_rejected(tmp_path: Path, filename: str) -> None:
    """A local file cannot silently lower the shared rules."""
    fixtures.create_package(tmp_path)
    (tmp_path / filename).write_text("# A local override\n", encoding=ENCODING)
    completed = fixtures.invoke(tmp_path, "parity")
    assert completed.returncode != 0
    assert "not permitted" in completed.stderr


@pytest.mark.parametrize("tool", ["ruff", "mypy", "vulture", "flake8"])
def test_pyproject_override_is_rejected(tmp_path: Path, tool: str) -> None:
    """Check pyproject tool sections as well as standalone tool files."""
    fixtures.create_package(tmp_path)
    (tmp_path / "pyproject.toml").write_text(f"[tool.{tool}]\n", encoding=ENCODING)
    completed = fixtures.invoke(tmp_path, "parity")
    assert completed.returncode != 0
    assert "cannot override" in completed.stderr


def test_profile_rejects_rule_override_fields() -> None:
    """Keep profile input limited to declared path and release selections."""
    document = tomllib.loads((fixtures.FIXTURES / "package.toml").read_text(encoding=ENCODING))
    document["ignore"] = ["ALL"]
    with pytest.raises(ValueError, match="Extra inputs"):
        ProjectProfile.model_validate(document)


def test_generation_rejects_output_link_escape(tmp_path: Path) -> None:
    """Do not follow a generated-directory link out of the selected package."""
    fixtures.create_package(tmp_path)
    (tmp_path / ".baqylau-dev").symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes the project"):
        parity.write_configuration(tmp_path, profiles.load_profile(tmp_path))
