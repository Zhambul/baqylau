# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject invalid extension declarations before dynamic entries reach tools."""

from pathlib import Path

import pytest
from baqylau_dev.models import Gate

from tests.dev_tools import package_fixture as fixtures

ENCODING = "utf-8"
ARCHITECTURE: Gate = "architecture"


@pytest.mark.parametrize(("before", "after", "message"), [
    ("def build_extension(", "def hidden_factory(", "factory must be one"),
    ("def build_extension(", "async def build_extension(", "synchronous function"),
    ("FeatureLifecycle(ExtensionLifecycle)", "FeatureLifecycle", "without declaring"),
    ("request: ActivationRequest", "renamed: ActivationRequest", "method signatures"),
    ("def deactivate(", "def release(", "method signatures"),
    ("-> ExtensionPlugin:", "-> int:", "assignment"),
])
def test_bad_backend_contract_is_rejected(tmp_path: Path, before: str, after: str, message: str) -> None:
    """A manifest root does not exempt a missing or wrongly typed implementation."""
    fixtures.create_package(tmp_path)
    path = tmp_path / "src" / "feature_backend.py"
    content = path.read_text(encoding=ENCODING).replace(before, after)
    path.write_text(content, encoding=ENCODING)
    completed = fixtures.invoke(tmp_path, ARCHITECTURE)
    assert completed.returncode != 0
    assert message in completed.stdout + completed.stderr


@pytest.mark.parametrize(("source", "message"), [
    ("from app import injection", "private host code"),
    ("from repository.impl import sqlite", "private host code"),
    ("from baqylau_extension_api import runtime", "private host code"),
    ("import sqlite3", "direct storage access"),
    ("import peer_feature.private", "undeclared dependency"),
])
def test_private_imports_are_rejected(tmp_path: Path, source: str, message: str) -> None:
    """Host, storage, and undeclared peer modules cannot be feature dependencies."""
    path = fixtures.create_package(tmp_path)
    path.write_text(f"{path.read_text(encoding=ENCODING)}\n{source}\n", encoding=ENCODING)
    completed = fixtures.invoke(tmp_path, ARCHITECTURE)
    assert completed.returncode != 0
    assert message in completed.stderr


def test_check_does_not_import_feature_code(tmp_path: Path) -> None:
    """Static architecture and type checks must not execute a module-level trap."""
    path = fixtures.create_package(tmp_path)
    path.write_text(
        f"{path.read_text(encoding=ENCODING)}\nraise RuntimeError('feature code was imported')\n", encoding=ENCODING,
    )
    completed = fixtures.invoke(tmp_path, ARCHITECTURE)
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_unselected_python_source_is_rejected(tmp_path: Path) -> None:
    """A package cannot hide feature source by selecting only a smaller root."""
    fixtures.create_package(tmp_path)
    (tmp_path / "hidden.py").write_text("import app\n", encoding=ENCODING)
    completed = fixtures.invoke(tmp_path, ARCHITECTURE)
    assert completed.returncode != 0
    assert "outside the declared roots" in completed.stderr
