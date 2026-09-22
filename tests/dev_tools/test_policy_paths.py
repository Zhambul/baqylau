# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject path escapes and host-only exclusions in external profiles."""

from pathlib import Path

import pytest
from baqylau_dev import profiles
from baqylau_dev.models import ProjectProfile

from tests.dev_tools import package_fixture as fixtures

SOURCE = "src"


@pytest.mark.parametrize("name", ["", "../outside", "/outside", "missing"])
def test_profile_rejects_invalid_paths(tmp_path: Path, name: str) -> None:
    """Do not inspect unrelated or absent paths through project configuration."""
    with pytest.raises(ValueError, match="path"):
        profiles.require_local_path(tmp_path, name)


@pytest.mark.parametrize("field", ["deadcode_roots", "deadcode_excludes", "type_only_roots"])
def test_extension_rejects_host_only_options(field: str) -> None:
    """The external profile cannot inherit host allowlists or unchecked roots."""
    with pytest.raises(ValueError, match="host-only"):
        ProjectProfile.model_validate({
            "policy_version": "0.1.0a1", "source_roots": [SOURCE], field: [SOURCE],
        })


def test_source_link_must_stay_inside_project(tmp_path: Path) -> None:
    """Resolve directory links before accepting a configured source path."""
    (tmp_path / SOURCE).symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(ValueError, match="invalid project path"):
        profiles.require_local_path(tmp_path, SOURCE)


def test_product_scan_cannot_include_tests(tmp_path: Path) -> None:
    """A broad source root must not turn test callers into product callers."""
    fixtures.create_package(tmp_path)
    path = tmp_path / profiles.PROFILE_FILE
    changed = path.read_text(encoding="utf-8").replace('["src"]', '["."]')
    path.write_text(changed, encoding="utf-8")
    with pytest.raises(ValueError, match="outside product roots"):
        profiles.load_profile(tmp_path)


def test_external_unit_gate_passes(tmp_path: Path) -> None:
    """Run the package's actual test module, not only its lint checks."""
    fixtures.create_package(tmp_path)
    completed = fixtures.invoke(tmp_path, "unit")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "2 passed" in completed.stdout
