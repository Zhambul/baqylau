"""Named harness catalog reads and catalog checks."""

from __future__ import annotations

from pytest_bdd import parsers, then, when

from sdk.client import BaqylauClient
from tests.e2e.testkit.references import HarnessCatalogs, HarnessLists


@when(parsers.parse('I read the installed harnesses as "{name}"'))
def read_installed_harnesses(
    client: BaqylauClient,
    harness_lists: HarnessLists,
    name: str,
) -> None:
    harness_lists.bind(name, client.harnesses.list())


@when(parsers.parse('I read the {harness} catalog as "{name}"'))
def read_harness_catalog(
    client: BaqylauClient,
    workspace: str,
    harness_catalogs: HarnessCatalogs,
    harness: str,
    name: str,
) -> None:
    harness_catalogs.bind(
        name,
        client.harnesses.catalog(harness, workspace=workspace),
    )


@then(parsers.parse('harness list "{name}" contains {harness}'))
def harness_list_contains(
    harness_lists: HarnessLists,
    name: str,
    harness: str,
) -> None:
    found = [item for item in harness_lists.get(name) if item.name == harness]
    assert len(found) == 1, f"harness list {name!r} has {len(found)} {harness!r} rows"


@then(parsers.parse('harness list "{name}" has exactly one default'))
def harness_list_has_one_default(harness_lists: HarnessLists, name: str) -> None:
    found = [item.name for item in harness_lists.get(name) if item.default_for_launch]
    assert len(found) == 1, f"harness list {name!r} has default harnesses {found}"


@then(parsers.parse('each harness in list "{name}" is launchable'))
def every_harness_is_launchable(harness_lists: HarnessLists, name: str) -> None:
    found = [item.name for item in harness_lists.get(name) if not item.launchable]
    assert not found, f"harness list {name!r} has harnesses that cannot launch: {found}"


@then(parsers.parse('harness {harness} in list "{name}" advertises control {control_name}'))
def harness_advertises_control(
    harness_lists: HarnessLists,
    name: str,
    harness: str,
    control_name: str,
) -> None:
    matches = [item for item in harness_lists.get(name) if item.name == harness]
    assert len(matches) == 1
    assert control_name in matches[0].control_names, (
        f"harness {harness!r} advertises controls {matches[0].control_names}"
    )


@then(parsers.parse("harness {harness} in list \"{name}\" advertises exactly controls '{control_names}'"))
def harness_advertises_exact_controls(
    harness_lists: HarnessLists,
    name: str,
    harness: str,
    control_names: str,
) -> None:
    matches = [item for item in harness_lists.get(name) if item.name == harness]
    assert len(matches) == 1
    expected = tuple(sorted(control_names.split(",")))
    assert matches[0].control_names == expected, (
        f"harness {harness!r} advertises controls {matches[0].control_names}; expected {expected}"
    )


@then(parsers.parse('catalog "{name}" has model {model} with effort {effort}'))
def catalog_has_model_effort(
    harness_catalogs: HarnessCatalogs,
    name: str,
    model: str,
    effort: str,
) -> None:
    models = [item for item in harness_catalogs.get(name).models if item.model_id == model]
    assert len(models) == 1, f"catalog {name!r} has {len(models)} models named {model!r}"
    efforts = [item.value for item in models[0].efforts]
    assert effort in efforts, f"model {model!r} offers efforts {efforts}"


@then(parsers.parse('catalog "{name}" has exactly one default model'))
def catalog_has_one_default_model(harness_catalogs: HarnessCatalogs, name: str) -> None:
    found = [item.model_id for item in harness_catalogs.get(name).models if item.default]
    assert len(found) == 1, f"catalog {name!r} has default models {found}"


@then(parsers.parse('each model in catalog "{name}" has exactly one default effort'))
def every_model_has_one_default_effort(
    harness_catalogs: HarnessCatalogs,
    name: str,
) -> None:
    failures = {
        model.model_id: [effort.value for effort in model.efforts if effort.default]
        for model in harness_catalogs.get(name).models
        if len([effort for effort in model.efforts if effort.default]) != 1
    }
    assert not failures, f"catalog {name!r} has invalid default efforts: {failures}"


@then(parsers.parse('catalog "{name}" has command {command}'))
def catalog_has_command(
    harness_catalogs: HarnessCatalogs,
    name: str,
    command: str,
) -> None:
    found = [item for item in harness_catalogs.get(name).commands if item.command == command]
    assert len(found) == 1, f"catalog {name!r} has {len(found)} commands named {command!r}"


@then(parsers.parse("catalog \"{name}\" advertises exactly rewind modes '{rewind_modes}'"))
def catalog_advertises_exact_rewind_modes(
    harness_catalogs: HarnessCatalogs,
    name: str,
    rewind_modes: str,
) -> None:
    actual = tuple(mode.value for mode in harness_catalogs.get(name).rewind_modes)
    expected = tuple(rewind_modes.split(","))
    assert actual == expected, f"catalog {name!r} advertises rewind modes {actual}; expected {expected}"
