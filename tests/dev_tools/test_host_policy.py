# Copyright (c) 2026 Zhambyl Yermagambet
"""Compare shared host rules with the saved pre-extraction configuration."""

import configparser
import tomllib
from pathlib import Path

from baqylau_dev import configuration, parity, profiles, resources

from tests.dev_tools import package_fixture as fixtures

ENCODING = "utf-8"
PRODUCT_ROOTS = frozenset((
    "api", "app", "bin", "client", "core", "dashboard", "audit", "domain", "engine", "extensions",
    "harness", "notify", "repository", "terminal", "packages/extension-api/src", "packages/extension-api-web/scripts",
))


def test_host_generated_configuration_is_current() -> None:
    """The committed editor configurations must match the policy and overlays."""
    parity.check_parity(fixtures.ROOT, profiles.load_profile(fixtures.ROOT))


def test_host_mypy_keeps_all_existing_options() -> None:
    """Retain global strict settings and every existing host exception."""
    before = configparser.ConfigParser()
    before.read(fixtures.FIXTURES / "host-mypy.ini", encoding=ENCODING)
    after = configparser.ConfigParser()
    after.read(fixtures.ROOT / "mypy.ini", encoding=ENCODING)
    for section in before.sections():
        current = section.replace(
            "tests.test_frontend_policy_inputs", "tests.test_frontend_policy_inputs,tests.dev_tools.*",
        ).replace("tests.extension_api.*", "tests.extension_api.*,tests.extension_host.*")
        assert dict(before[section]) == dict(after[current])
    assert len(before.sections()) == len(after.sections())


def test_host_design_options_are_unchanged() -> None:
    """Keep the same WPS selection, vocabulary, length, and excluded paths."""
    assert _design_settings(fixtures.FIXTURES / "host-flake8.ini") == _design_settings(fixtures.ROOT / "setup.cfg")


def _design_settings(path: Path) -> dict[str, str]:
    parser = configparser.ConfigParser()
    parser.read(path, encoding=ENCODING)
    return {
        name: setting.replace("\n", "").strip(",")
        for name, setting in parser["flake8"].items()
    }


def test_host_ruff_retains_every_effective_option() -> None:
    """Combine only the declared Ruff sections and compare the prior full input."""
    shared = tomllib.loads(resources.policy_text("ruff.toml"))
    profile = profiles.load_profile(fixtures.ROOT)
    current = tomllib.loads(configuration.ruff_configuration(fixtures.ROOT, profile))
    current.pop("extend")
    shared_lint = shared.pop("lint")
    current_lint = current.pop("lint")
    current_lint["per-file-ignores"].update(current_lint.pop("extend-per-file-ignores"))
    assert tomllib.loads((fixtures.FIXTURES / "host-ruff.toml").read_text(encoding=ENCODING)) == {
        **shared, **current, "lint": {**shared_lint, **current_lint},
    }


def test_host_type_and_product_roots_are_explicit() -> None:
    """Add policy source without moving tests into the dead-code product scan."""
    profile = profiles.load_profile(fixtures.ROOT)
    assert "tests" not in profile.source_roots
    assert profile.test_roots == ("tests",)
    assert profile.type_only_roots == ("sdk",)
    assert set(profile.source_roots) == PRODUCT_ROOTS | {"packages/dev-tools/src"}
