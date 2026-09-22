# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject invalid or stale settings before saving values or starting preparation."""

from contextlib import closing
from pathlib import Path

import pytest
from baqylau_extension_api.models.scopes import SessionScope

from extensions.lifecycle_control_contract import LifecycleConflictError, LifecycleRequestError
from tests.extension_api import samples
from tests.extension_host import (
    lifecycle_control_fixture as controls,
    lifecycle_dependency_fixture as packages,
    settings_control_fixture as fixture,
)

CUSTOM = '"chosen"'


@pytest.mark.parametrize("field", ["expected_revision", "expected_catalog_revision", "expected_settings_revision"])
def test_settings_reject_stale_revisions(tmp_path: Path, field: str) -> None:
    """Each independent compare-and-set value is checked before admission."""
    packages.write_settings_package(tmp_path)
    with closing(controls.open_control(tmp_path)) as case:
        selected = fixture.request(case, "stale", CUSTOM).model_copy(update={field: 900})
        with pytest.raises(LifecycleConflictError, match="revision changed"):
            fixture.service(case).change_settings(fixture.OWNER, selected)
        assert fixture.snapshot(case).settings_revision == 0
        assert fixture.snapshot(case).pending_operation is None


@pytest.mark.parametrize("document", ["12", "false", "null", "{}", "malformed private input"])
def test_bad_documents_have_bounded_error(tmp_path: Path, document: str) -> None:
    """Invalid content is rejected without copying its contents to the failure message."""
    packages.write_settings_package(tmp_path)
    with closing(controls.open_control(tmp_path)) as case:
        selected = fixture.request(case, "invalid", document)
        with pytest.raises(LifecycleRequestError, match="settings change does not match") as failed:
            fixture.service(case).change_settings(fixture.OWNER, selected)
        assert document not in str(failed.value)
        assert fixture.snapshot(case).settings_revision == 0


def test_settings_reject_another_digest(tmp_path: Path) -> None:
    """A valid settings document cannot authorize different selected package bytes."""
    packages.write_settings_package(tmp_path)
    with closing(controls.open_control(tmp_path)) as case:
        selected = fixture.request(case, "wrong-package", CUSTOM)
        selected = selected.model_copy(update={"package_digest": samples.DIGEST})
        with pytest.raises(LifecycleConflictError, match="bytes differ"):
            fixture.service(case).change_settings(fixture.OWNER, selected)


def test_settings_reject_undeclared_scope(tmp_path: Path) -> None:
    """A package which declares workspace settings does not gain session overrides."""
    packages.write_settings_package(tmp_path)
    with closing(controls.open_control(tmp_path)) as case:
        selected = fixture.request(case, "wrong-scope", CUSTOM).model_copy(update={
            "scope": SessionScope(session_id="session-one", actor_id="actor-one", harness="test-harness"),
        })
        with pytest.raises(LifecycleRequestError, match="does not declare this settings scope"):
            fixture.service(case).change_settings(fixture.OWNER, selected)
