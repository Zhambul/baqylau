# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep accepted settings and published runtime values in the same transaction."""

from contextlib import closing
from pathlib import Path

from tests.extension_host import (
    lifecycle_control_fixture as controls,
    lifecycle_dependency_fixture as packages,
    settings_control_fixture as fixture,
)

CUSTOM = '"custom setting"'
RESET_REVISION = 2


def test_settings_read_does_not_save_defaults(tmp_path: Path) -> None:
    """A read does not save manifest defaults as explicit user choices."""
    packages.write_settings_package(tmp_path)
    with closing(controls.open_control(tmp_path)) as case:
        before = case.host.controller.read_state()
        current = fixture.snapshot(case)
        assert current.override is None and current.settings_revision == 0
        assert current.effective == current.definition.defaults
        assert not current.selected_from_committed
        assert case.host.controller.read_state() == before


def test_pending_settings_are_not_read_as_saved(tmp_path: Path) -> None:
    """An accepted candidate remains private until the engine publishes it."""
    packages.write_settings_package(tmp_path)
    with closing(controls.open_control(tmp_path)) as case:
        selected = fixture.request(case, "pending", CUSTOM)
        admitted = fixture.service(case).change_settings(fixture.OWNER, selected)
        assert admitted.operation is not None
        current = fixture.snapshot(case)
        assert current.pending_operation == admitted.operation.proposal.operation_id
        assert current.override is None and current.settings_revision == 0
        case.host.finish()
        assert fixture.snapshot(case).effective.json_text == CUSTOM


def test_disabled_settings_do_not_enable_package(tmp_path: Path) -> None:
    """Saved disabled values are applied when a later request enables the package."""
    packages.write_settings_package(tmp_path)
    with closing(controls.open_control(tmp_path)) as case:
        fixture.save(case, "disabled-value", CUSTOM)
        assert not case.host.controller.read_state().lifecycle.intents
        assert not fixture.snapshot(case).selected_from_committed
        fixture.enable(case)
        current = fixture.snapshot(case)
        assert current.selected_from_committed and current.effective.json_text == CUSTOM
        fixture.require_runtime_settings(case, current)


def test_reset_removes_choice_and_uses_defaults(tmp_path: Path) -> None:
    """Reset saves absence, not a copy of the current default document."""
    packages.write_settings_package(tmp_path)
    with closing(controls.open_control(tmp_path)) as case:
        fixture.enable(case)
        fixture.save(case, "explicit", CUSTOM)
        fixture.save(case, "reset", None)
        current = fixture.snapshot(case)
        assert current.override is None
        assert current.effective == current.definition.defaults
        assert current.settings_revision == RESET_REVISION
