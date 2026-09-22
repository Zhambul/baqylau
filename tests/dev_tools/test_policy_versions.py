# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject policy release and installed-tool drift without changing global tools."""

from pathlib import Path

import pytest
from baqylau_dev import profiles, resources
from baqylau_dev.models import ToolVersion

from tests.dev_tools import package_fixture as fixtures


@pytest.mark.parametrize("installed", ["missing", "0.0.0", "0.15.22"])
def test_changed_tool_version_fails_parity(monkeypatch: pytest.MonkeyPatch, installed: str) -> None:
    """The expected exact pin is required, not merely a compatible version range."""
    versions = (ToolVersion(name="ruff", required="==0.15.21", installed=installed),)
    monkeypatch.setattr(resources, "tool_versions", lambda: versions)
    with pytest.raises(ValueError, match="ruff: expected"):
        resources.require_tool_versions()


def test_policy_digest_covers_resource_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Changing a policy byte must change the report digest."""
    original = resources.policy_digest()
    monkeypatch.setattr(resources, "policy_text", lambda name: f"changed: {name}")
    assert resources.policy_digest() != original


def test_project_release_must_match_installed(tmp_path: Path) -> None:
    """Do not silently run a package with a different policy release."""
    fixtures.create_package(tmp_path)
    path = tmp_path / profiles.PROFILE_FILE
    changed = path.read_text(encoding="utf-8").replace("0.1.0a1", "0.2.0")
    path.write_text(changed, encoding="utf-8")
    with pytest.raises(ValueError, match="policy version differs"):
        profiles.load_profile(tmp_path)
