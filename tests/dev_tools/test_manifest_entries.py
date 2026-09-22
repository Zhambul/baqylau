# Copyright (c) 2026 Zhambyl Yermagambet
"""Check declared file paths and prevent stale dynamic roots."""

from pathlib import Path

import pytest
from baqylau_dev.extension_checks import check_extension
from baqylau_dev.profiles import load_profile
from baqylau_extension_api.manifest.package import ExtensionManifest

from tests.dev_tools import package_fixture as fixtures

ENCODING = "utf-8"


@pytest.mark.parametrize(("before", "after", "message"), [
    ("feature_backend", "unselected_backend", "backend module must resolve"),
    ("tests/e2e/test_worker.py", "tests/missing.py", "does not exist"),
    ("tests/e2e/test_worker.py", "src/sample_feature.py", "outside declared test roots"),
    ('"quality_policy": "0.1.0a1"', '"quality_policy": "0.2.0"', "manifest quality policy"),
    ('"api_requires": "==0.1.0a1"', '"api_requires": ">=99"', "API range"),
])
def test_bad_manifest_entry_is_rejected(tmp_path: Path, before: str, after: str, message: str) -> None:
    """Resolve declarations against the package, not the host checkout."""
    fixtures.create_package(tmp_path)
    path = tmp_path / "extension.json"
    content = path.read_text(encoding=ENCODING).replace(before, after)
    path.write_text(content, encoding=ENCODING)
    with pytest.raises(ValueError, match=message):
        check_extension(tmp_path, load_profile(tmp_path))


def test_missing_e2e_declaration_is_rejected(tmp_path: Path) -> None:
    """A backend needs package-owned test declarations even before dispatch exists."""
    fixtures.create_package(tmp_path)
    path = tmp_path / "extension.json"
    manifest = ExtensionManifest.model_validate_json(path.read_bytes())
    invalid = manifest.model_copy(update={"e2e": ()}).model_dump_json()
    path.write_text(invalid, encoding=ENCODING)
    with pytest.raises(ValueError, match="e2e"):
        check_extension(tmp_path, load_profile(tmp_path))


def test_dynamic_exception_is_location_specific(tmp_path: Path) -> None:
    """A real activate method must not hide unrelated dead methods with that name."""
    path = fixtures.create_package(tmp_path)
    path.write_text(
        f"{path.read_text(encoding=ENCODING)}\n"
        "class Unrelated:\n    def activate(self, request: int) -> int:\n        return request\n\nUnrelated()\n",
        encoding=ENCODING,
    )
    completed = fixtures.invoke(tmp_path, "deadcode")
    assert completed.returncode != 0
    assert "unused method 'activate'" in completed.stdout
    assert "sample_feature.py" in completed.stdout


def test_stale_factory_exception_cannot_survive(tmp_path: Path) -> None:
    """Regenerate entries so an old factory name becomes normal dead product code."""
    fixtures.create_package(tmp_path)
    path = tmp_path / "src" / "feature_backend.py"
    path.write_text(
        f"{path.read_text(encoding=ENCODING)}\n"
        "def replacement(services: ExtensionHostServices) -> ExtensionPlugin:\n"
        "    return FeaturePlugin(services.environment.extension_info)\n"
        , encoding=ENCODING,
    )
    assert fixtures.invoke(tmp_path, "deadcode").returncode != 0
    manifest = tmp_path / "extension.json"
    content = manifest.read_text(encoding=ENCODING).replace("build_extension", "replacement")
    manifest.write_text(content, encoding=ENCODING)
    completed = fixtures.invoke(tmp_path, "deadcode")
    assert completed.returncode != 0
    assert "unused function 'build_extension'" in completed.stdout
