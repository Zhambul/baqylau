# Copyright (c) 2026 Zhambyl Yermagambet
"""Check discovery without a central list of harness names."""

import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest

from dashboard import cli_options, cli_output
from domain.ids import HarnessName
from harness.impl import definitions, discovery
from harness.impl.claude_code.plugin import plugin
from harness.models.definition import HarnessDefinition
from harness.runtime import HarnessRuntimeConfig, default_harness_runtime_configs

NAME = HarnessName("test_provider")


@pytest.fixture
def installed_definition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Install a third plugin through the existing directory discovery seam.

    Returns:
        The factory used to check the resolved runtime configuration.

    """
    directory = tmp_path / NAME
    directory.mkdir()
    (directory / "plugin.py").touch()
    monkeypatch.setattr(definitions, "PLUGIN_DIRECTORY", tmp_path)
    declaration = ModuleType(f"harness.impl.{NAME}.definition")
    monkeypatch.setattr(
        declaration, "DEFINITION",
        HarnessDefinition(NAME, lambda: HarnessRuntimeConfig("test-provider", tmp_path)), raising=False,
    )
    descriptor = replace(plugin.harness_info, name=NAME)
    factory = Mock(return_value=replace(plugin, harness_info=descriptor))
    implementation = ModuleType(f"harness.impl.{NAME}.plugin")
    monkeypatch.setattr(implementation, "build_plugin", factory, raising=False)
    monkeypatch.setitem(sys.modules, declaration.__name__, declaration)
    monkeypatch.setitem(sys.modules, implementation.__name__, implementation)
    return factory


def test_third_plugin_needs_no_registration(installed_definition: Mock) -> None:
    """Use plugin-owned defaults for a previously unknown harness name."""
    assert definitions.definitions()[0].name == NAME
    assert discovery.installed()[0].harness_info.name == NAME
    assert installed_definition.call_args.args[0].executable == "test-provider"


def test_discovery_preserves_runtime_overrides(installed_definition: Mock, tmp_path: Path) -> None:
    """Pass an explicit runtime to the discovered plugin factory."""
    runtime = HarnessRuntimeConfig("custom-provider", tmp_path / "custom")
    discovery.installed(default_harness_runtime_configs().updated(NAME, runtime))
    assert installed_definition.call_args.args[0] == runtime


@pytest.mark.usefixtures("installed_definition")
def test_cli_uses_discovered_names(tmp_path: Path) -> None:
    """Accept a new plugin name and reject an unregistered name."""
    executable = str(tmp_path / "custom-provider")
    options = cli_options.launch_options(["--harness-executable", f"{NAME}={executable}"])
    assert options.harness_runtime_configs.for_harness(NAME).executable == executable
    with pytest.raises(cli_output.UsageError, match="unknown harness"):
        cli_options.launch_options(["--harness-executable", "missing=custom-provider"])


def test_factory_cannot_change_its_declared_name(installed_definition: Mock) -> None:
    """Reject a plugin that returns a different harness."""
    installed_definition.return_value = plugin
    with pytest.raises(ValueError, match="different harness name"):
        discovery.installed()


def test_definition_must_match_directory(installed_definition: Mock, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject a missing declaration before the factory can run."""
    monkeypatch.setattr(sys.modules[f"harness.impl.{NAME}.definition"], "DEFINITION", None)
    with pytest.raises(TypeError, match="DEFINITION"):
        discovery.installed()
    installed_definition.assert_not_called()
