# Copyright (c) 2026 Zhambyl Yermagambet
"""Resolve declared test roots without extending exemptions to product code."""

from pathlib import Path

from baqylau_dev import profiles

from tests.dev_tools import package_fixture as fixtures

ENCODING = "utf-8"


def test_custom_test_root_uses_common_rules(tmp_path: Path) -> None:
    """Keep the shared pytest assertion rules when tests have another directory."""
    fixtures.create_package(tmp_path)
    (tmp_path / "tests").rename(tmp_path / "checks")
    path = tmp_path / profiles.PROFILE_FILE
    changed = path.read_text(encoding=ENCODING).replace('["tests"]', '["checks"]')
    path.write_text(changed, encoding=ENCODING)
    manifest = tmp_path / "extension.json"
    content = manifest.read_text(encoding=ENCODING).replace("tests/", "checks/")
    manifest.write_text(content, encoding=ENCODING)
    completed = fixtures.invoke(tmp_path, "lint")
    assert completed.returncode == 0, completed.stdout + completed.stderr
