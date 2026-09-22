# Copyright (c) 2026 Zhambyl Yermagambet
"""Apply extension-only write policy before admission without blocking metadata reads."""

from contextlib import closing
from pathlib import Path

import pytest

from api.runtime_config import ApplicationConfig
from extensions.configuration import READ_ONLY_ENVIRONMENT, configured_read_only
from extensions.control_policy import ExtensionControlPolicy, ExtensionReadOnlyError
from extensions.lifecycle_control import LifecycleControl
from extensions.models.lifecycle_requests import plan_request
from tests.extension_host import lifecycle_control_fixture as controls, package_fixture

ENABLED = "1"


def test_read_only_denies_new_and_replayed_change(tmp_path: Path) -> None:
    """Read-only mode does not admit work even when the request ID already exists."""
    package_fixture.write_package(tmp_path / "packages", web=True)
    with closing(controls.open_control(tmp_path)) as case:
        request = case.request("enable", "original")
        readonly = LifecycleControl(case.control.manager, case.control.catalog, ExtensionControlPolicy(read_only=True))
        assert readonly.preview_lifecycle(package_fixture.OWNER, plan_request(request)).affected_extensions
        with pytest.raises(ExtensionReadOnlyError):
            readonly.change_lifecycle(package_fixture.OWNER, request)
        case.control.change_lifecycle(package_fixture.OWNER, request)
        case.host.finish()
        with pytest.raises(ExtensionReadOnlyError):
            readonly.change_lifecycle(package_fixture.OWNER, request)


@pytest.mark.parametrize("configured", ["", "true", "false", "yes", "2"])
def test_read_only_rejects_ambiguous_environment(configured: str) -> None:
    """A misspelled policy value cannot silently enable extension writes."""
    with pytest.raises(ValueError, match="must be 0 or 1"):
        configured_read_only({READ_ONLY_ENVIRONMENT: configured})


def test_private_config_has_explicit_read_only(tmp_path: Path) -> None:
    """Explicit private application choices override inherited extension policy."""
    config = ApplicationConfig(data_directory=tmp_path, base_environment={READ_ONLY_ENVIRONMENT: ENABLED})
    assert config.process_environment()[READ_ONLY_ENVIRONMENT] == "0"
    selected = ApplicationConfig(data_directory=tmp_path, extension_read_only=True)
    assert selected.process_environment()[READ_ONLY_ENVIRONMENT] == ENABLED


def test_environment_config_keeps_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """The normal daemon launch reads and forwards the configured extension policy."""
    monkeypatch.setenv(READ_ONLY_ENVIRONMENT, ENABLED)
    config = ApplicationConfig.from_environment()
    assert config.extension_read_only
    assert config.process_environment()[READ_ONLY_ENVIRONMENT] == ENABLED
