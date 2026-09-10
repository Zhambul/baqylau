# Copyright (c) 2026 Zhambyl Yermagambet
"""Harness catalog and static menu tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain.ids import HarnessName
from harness.impl.claude_code.plugin import plugin as claude_plugin
from harness.impl.codex.plugin import plugin as codex_plugin
from harness.models.catalog import QueryContext
from tests.canonical_runtime import ProviderGraph
from tests.plugin_tests import vocabulary as fixture

if TYPE_CHECKING:
    from pathlib import Path


def test_catalogs_expose_only_what_depends_on_dir(tmp_path: Path) -> None:
    """The catalogue is now the per-DIRECTORY half of the menu vocabulary.

    Everything a harness offers unconditionally moved onto HarnessInfo, which is
    a frozen literal built at import -- so only the slash commands, discovered by
    walking the session's own directory, still need a QueryContext.
    """
    application = ProviderGraph()
    context = QueryContext(session_id=None, working_directory=str(tmp_path))

    claude_catalog = application.catalog.read(HarnessName.CLAUDE_CODE, context)
    codex_catalog = application.catalog.read(HarnessName.CODEX, context)

    assert {command.command for command in claude_catalog.commands} != {
        command.command for command in codex_catalog.commands
    }
    assert not hasattr(claude_catalog, "models")
    assert not hasattr(claude_catalog, "accounts")


def test_static_menu_vocabulary_lives_on_harness() -> None:
    """Verify static menu vocabulary lives on the harness descriptor."""
    assert [model.model_name for model in claude_plugin.harness_info.models] == [
        fixture.FABLE,
        "opus",
        "sonnet",
        "haiku",
        "claude-opus-4-6",
    ]
    assert all(model.model_name.startswith("gpt-") for model in codex_plugin.harness_info.models)
    assert claude_plugin.harness_info.rewind_modes
    assert [mode.mode for mode in codex_plugin.harness_info.rewind_modes] == ["conversation"]
    # Both harnesses use their one default login.
    assert not any((claude_plugin.harness_info.supports_accounts, codex_plugin.harness_info.supports_accounts))


def test_reasoning_levels_belong_to_model_that() -> None:
    """A level a model does not have must not be advertised for it.

    Measured on the live picker: one codex model's advanced sub-step holds Max
    alone, with no Ultra row, while its siblings list both. The old flat
    per-harness list promised Ultra for every model, so the menu offered a level
    the picker would then refuse.
    """
    by_id = {model.model_name: model for model in codex_plugin.harness_info.models}
    luna = {effort.effort for effort in by_id[fixture.GPT_FIVE_SIX_LUNA].efforts}
    sol = {effort.effort for effort in by_id["gpt-5.6-sol"].efforts}

    assert "ultra" not in luna
    assert "ultra" in sol
    # every model still names exactly one default
    for model in codex_plugin.harness_info.models:
        assert len([effort for effort in model.efforts if effort.default]) == 1
