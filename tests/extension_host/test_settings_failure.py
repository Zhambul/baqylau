# Copyright (c) 2026 Zhambyl Yermagambet
"""Retain accepted settings when preparation or mutation policy rejects an edit."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from extensions.control_policy import ExtensionControlPolicy, ExtensionReadOnlyError
from extensions.models.settings_requests import SettingsReadRequest
from tests.extension_host import (
    lifecycle_control_fixture as controls,
    lifecycle_dependency_fixture as packages,
    settings_control_fixture as fixture,
)


def test_failed_settings_keep_prior_runtime(tmp_path: Path) -> None:
    """Failure records its operation but does not commit the candidate settings."""
    packages.write_settings_package(tmp_path)
    with closing(controls.open_control(tmp_path)) as case:
        fixture.enable(case)
        fixture.save(case, "accepted", '"accepted value"')
        previous = case.host.controller.read_state().active_runtime
        selected = fixture.request(case, "failed", '"must not be saved"')
        with patch.object(ThreadPoolExecutor, "submit", side_effect=RuntimeError("test executor stopped")):
            admitted = fixture.service(case).change_settings(fixture.OWNER, selected)
        case.host.finish("failed")
        assert admitted.operation is not None
        assert fixture.snapshot(case).effective.json_text == '"accepted value"'
        assert fixture.snapshot(case).settings_revision == 1
        assert case.host.controller.read_state().active_runtime == previous
        assert fixture.service(case).change_settings(fixture.OWNER, selected).status == "replayed"


def test_read_only_settings_allow_reads_not_edits(tmp_path: Path) -> None:
    """The service applies the same write policy to new and exact-replayed edits."""
    packages.write_settings_package(tmp_path)
    with closing(controls.open_control(tmp_path)) as case:
        selected = fixture.save(case, "accepted", '"saved"')
        readonly = replace(fixture.service(case), policy=ExtensionControlPolicy(read_only=True))
        assert readonly.read_settings(fixture.OWNER, SettingsReadRequest()).settings_revision == 1
        with pytest.raises(ExtensionReadOnlyError):
            readonly.change_settings(fixture.OWNER, selected)
        with pytest.raises(ExtensionReadOnlyError):
            readonly.change_settings(fixture.OWNER, fixture.request(case, "new", None))
