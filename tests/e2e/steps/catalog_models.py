# Copyright (c) 2026 Zhambyl Yermagambet
"""Steps that check models and commands in a harness catalog."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pytest_bdd import parsers, then

if TYPE_CHECKING:
    from tests.e2e.testkit.references import HarnessCatalogs


@then(parsers.parse('catalog "{name}" has model {model} with effort {effort}'))
def catalog_has_model_effort(harness_catalogs: HarnessCatalogs, name: str, model: str, effort: str) -> None:
    """Verify a model offers an effort."""
    models = [model_option for model_option in harness_catalogs.get(name).models if model_option.model_id == model]
    assert len(models) == 1, f"catalog {name!r} has {len(models)} models named {model!r}"
    efforts = [model_effort.effort for model_effort in models[0].efforts]
    assert effort in efforts, f"model {model!r} offers efforts {efforts}"


@then(parsers.parse('catalog "{name}" has exactly one default model'))
def catalog_has_one_default_model(harness_catalogs: HarnessCatalogs, name: str) -> None:
    """Verify exactly one catalog model is the default."""
    found = [model_option.model_id for model_option in harness_catalogs.get(name).models if model_option.default]
    assert len(found) == 1, f"catalog {name!r} has default models {found}"


@then(parsers.parse('each model with effort settings in catalog "{name}" has exactly one default effort'))
def every_model_has_one_default_effort(harness_catalogs: HarnessCatalogs, name: str) -> None:
    """Verify each catalog model has one default effort."""
    failures = {
        model.model_id: [model_effort.effort for model_effort in model.efforts if model_effort.default]
        for model in harness_catalogs.get(name).models
        if model.efforts and len([model_effort for model_effort in model.efforts if model_effort.default]) != 1
    }
    assert not failures, f"catalog {name!r} has invalid default efforts: {failures}"


@then(parsers.parse('catalog "{name}" has command {command}'))
def catalog_has_command(harness_catalogs: HarnessCatalogs, name: str, command: str) -> None:
    """Verify the catalog contains one command."""
    found = [
        command_option for command_option in harness_catalogs.get(name).commands if command_option.command == command
    ]
    assert len(found) == 1, f"catalog {name!r} has {len(found)} commands named {command!r}"


@then(parsers.re(r"catalog \"(?P<name>[^\"]+)\" advertises exactly rewind modes '(?P<rewind_modes>[^']*)'"))
def catalog_advertises_exact_rewind_modes(
    harness_catalogs: HarnessCatalogs,
    name: str,
    rewind_modes: str,
) -> None:
    """Verify the catalog advertises the specified rewind modes."""
    actual = tuple(mode.mode for mode in harness_catalogs.get(name).rewind_modes)
    expected = tuple(rewind_modes.split(",")) if rewind_modes else ()
    assert actual == expected, f"catalog {name!r} advertises rewind modes {actual}; expected {expected}"
