# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep settings edits bound to retained active bytes, even after source changes."""

from contextlib import closing
from pathlib import Path

import pytest

from extensions.lifecycle_control_contract import LifecycleConflictError
from tests.extension_host import (
    lifecycle_control_fixture as controls,
    lifecycle_dependency_fixture as packages,
    package_fixture,
    settings_control_fixture as fixture,
)


def test_settings_keep_active_bytes_after_rescan(tmp_path: Path) -> None:
    """A settings edit cannot silently select the source package's newer digest."""
    source = packages.write_settings_package(tmp_path)
    with closing(controls.open_control(tmp_path)) as case:
        fixture.enable(case)
        previous = fixture.snapshot(case).extension_info
        package_fixture.write_file(source, "new-source.txt", b"new source version")
        case.rescan()
        fixture.save(case, "retained", '"new setting with old bytes"')
        assert fixture.snapshot(case).extension_info == previous
        changed = fixture.request(case, "wrong-version", None).model_copy(update={
            "package_digest": case.request("reload", "read-digest").package_digest,
        })
        with pytest.raises(LifecycleConflictError, match="reload selects new bytes"):
            fixture.service(case).change_settings(fixture.OWNER, changed)


def test_settings_use_retained_missing_source(tmp_path: Path) -> None:
    """An enabled package can change settings after its source directory is removed."""
    packages.write_settings_package(tmp_path)
    with closing(controls.open_control(tmp_path)) as case:
        fixture.enable(case)
        (tmp_path / "packages").rename(tmp_path / "removed-source")
        case.rescan()
        fixture.save(case, "without-source", '"retained package setting"')
        assert fixture.snapshot(case).effective.json_text == '"retained package setting"'
        assert fixture.snapshot(case).selected_from_committed
