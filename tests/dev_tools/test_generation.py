# Copyright (c) 2026 Zhambyl Yermagambet
"""Detect changed generated host files before executing a linter."""

import shutil
import tomllib
from pathlib import Path

import pytest
from baqylau_dev import commands, configuration, parity, profiles, resources
from baqylau_dev.models import ProjectProfile

from tests.dev_tools import package_fixture as fixtures


def _host_profile(root: Path) -> ProjectProfile:
    profile = profiles.load_profile(fixtures.ROOT)
    policy_path = root / configuration.HOST_RUFF_RESOURCE
    policy_path.parent.mkdir(parents=True)
    shutil.copyfile(resources.POLICY_ROOT / "ruff.toml", policy_path)
    shutil.copytree(fixtures.ROOT / "quality", root / "quality")
    parity.write_configuration(root, profile)
    return profile


@pytest.mark.parametrize("filename", ["ruff.toml", "mypy.ini", "setup.cfg"])
def test_generated_host_drift_is_rejected(tmp_path: Path, filename: str) -> None:
    """Even a local edit must go through the authoritative policy or host overlay."""
    profile = _host_profile(tmp_path)
    parity.check_parity(tmp_path, profile)
    target = tmp_path / filename
    target.write_text(f"{target.read_text(encoding='utf-8')}\n# local edit\n", encoding="utf-8")
    with pytest.raises(ValueError, match="configuration drift"):
        parity.check_parity(tmp_path, profile)


def test_host_unit_gate_excludes_live_suites() -> None:
    """A quality unit command must not start token-spending or real Kitty cases."""
    profile = profiles.load_profile(fixtures.ROOT)
    arguments = commands.tool_arguments(fixtures.ROOT, profile, "unit")
    assert "--ignore=tests/e2e" in arguments
    assert "not kitty" in arguments


def test_generated_base_has_no_host_path(tmp_path: Path) -> None:
    """The external config points to installed policy, not a sibling host file."""
    fixtures.create_package(tmp_path)
    profile = profiles.load_profile(tmp_path)
    generated = configuration.ruff_configuration(tmp_path, profile)
    configured = Path(tomllib.loads(generated)["extend"])
    assert configured.is_absolute()
    assert configured == resources.POLICY_ROOT / "ruff.toml"
    assert "client/*.py" not in generated
