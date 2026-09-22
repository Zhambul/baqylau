# Copyright (c) 2026 Zhambyl Yermagambet
"""Check package source selection without importing backend modules in the test process."""

from pathlib import Path

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.runtime.package_loading import backend_source_directory

MODULE_NAME = "example"
MODULE_FILE = "example.py"
SOURCE_CHOICES = (MODULE_FILE, "example/__init__.py", "src/example.py", "src/example/__init__.py")


@pytest.mark.parametrize("relative", SOURCE_CHOICES)
def test_backend_selects_one_source_root(tmp_path: Path, relative: str) -> None:
    """Flat modules and package layouts retain normal Python imports."""
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("raise AssertionError('selection must not import')", encoding="utf-8")
    expected = tmp_path / "src" if relative.startswith("src/") else tmp_path
    assert backend_source_directory(tmp_path, MODULE_NAME) == expected


def test_backend_rejects_ambiguous_layout(tmp_path: Path) -> None:
    """The worker uses the same single-file selection rule as discovery."""
    (tmp_path / MODULE_FILE).write_bytes(b"")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / MODULE_FILE).write_bytes(b"")
    with pytest.raises(ExtensionContractError, match="one package-owned"):
        backend_source_directory(tmp_path, MODULE_NAME)


def test_backend_rejects_source_links(tmp_path: Path) -> None:
    """A module link cannot select mutable code outside the fixed artifact."""
    (tmp_path / "other.py").write_bytes(b"")
    (tmp_path / MODULE_FILE).symlink_to(tmp_path / "other.py")
    with pytest.raises(ExtensionContractError, match="links"):
        backend_source_directory(tmp_path, MODULE_NAME)


def test_backend_rejects_relative_root() -> None:
    """The working directory cannot supply the selected package implicitly."""
    with pytest.raises(ExtensionContractError, match="absolute"):
        backend_source_directory(Path("relative"), MODULE_NAME)
